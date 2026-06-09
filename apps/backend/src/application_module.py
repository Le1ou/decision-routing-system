import psycopg
from psycopg import sql as pgsql
import  psycopg_pool
import atexit
import json
from pathlib import Path
from datetime import datetime, timezone
import base64
from psycopg.rows import dict_row
import ldap3
from fastapi import FastAPI, Depends, HTTPException, status, Body
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import os

configPath = Path(__file__).parent.parent / "config.json"
global configData
project_timezone = timezone.utc
with configPath.open(encoding="utf-8") as config_data:
    configData = json.load(config_data)
    config_data.close()

security = HTTPBasic()


def _auth_mode() -> str:
    """Active authentication backend: 'mock' (default) or 'ad'.

    Read from the AUTH_MODE environment variable first, then config.json, so the
    backend can be switched to a real Active Directory by flipping a single value
    (env AUTH_MODE=ad, or "AUTH_MODE": "ad" in config.json) — no code changes.
    The default stays 'mock' so local dev and the test suite need no AD server.
    """
    return (os.environ.get("AUTH_MODE") or configData.get("AUTH_MODE") or "mock").strip().lower()


# How long (seconds) to wait for the AD server before treating a login as failed,
# so a misconfigured/unreachable server fails fast instead of hanging requests.
AD_CONNECT_TIMEOUT = 10


def _ad_config() -> dict:
    """Resolve Active Directory connection settings.

    Environment variables win over config.json so the AD server can be supplied at
    deploy time without editing committed files:

        AD_SERVER   ← AD_auth.server_adress   host name or IP of the domain controller
        AD_DOMAIN   ← AD_auth.Domain          AD domain for the UPN  user@DOMAIN
        AD_USE_SSL  ← AD_auth.use_ssl          default True (LDAPS) — never bind in clear text
        AD_PORT     ← AD_auth.port             default 636 (SSL) / 389 (plain)
    """
    cfg = configData.get("AD_auth", {})

    def _as_bool(value, default):
        # Treat unset / empty-string env vars as "not provided" so they fall back
        # to the default instead of being read as False.
        if value is None or str(value).strip() == "":
            return default
        return str(value).strip().lower() in ("1", "true", "yes", "on")

    server  = (os.environ.get("AD_SERVER") or cfg.get("server_adress") or "").strip()
    domain  = (os.environ.get("AD_DOMAIN") or cfg.get("Domain") or "").strip()
    use_ssl = _as_bool(os.environ.get("AD_USE_SSL"), bool(cfg.get("use_ssl", True)))
    port_raw = os.environ.get("AD_PORT") or cfg.get("port")
    try:
        port = int(port_raw) if port_raw else (636 if use_ssl else 389)
    except (TypeError, ValueError):
        port = 636 if use_ssl else 389

    # Optional: derive the application role from AD group membership instead of the
    # system directory. "system" (default) keeps the role assigned at onboarding;
    # "ad" reads the user's groups and maps them via group_role_map.
    role_source = (os.environ.get("AD_ROLE_SOURCE") or cfg.get("role_source") or "system").strip().lower()
    search_base = (os.environ.get("AD_SEARCH_BASE") or cfg.get("search_base") or "").strip()
    group_role_map = cfg.get("group_role_map") or {}
    return {"server": server, "domain": domain, "use_ssl": use_ssl, "port": port,
            "role_source": role_source, "search_base": search_base,
            "group_role_map": group_role_map}


def _domain_to_base(domain: str) -> str:
    """Convert a DNS domain (company.local) to an LDAP base DN (DC=company,DC=local)."""
    parts = [p for p in domain.split(".") if p]
    return ",".join(f"DC={p}" for p in parts)


def _map_groups_to_role(group_dns, group_role_map: dict, ladder: list):
    """Pick the highest application role whose group_role_map key matches one of
    the user's AD group DNs (case-insensitive substring match, so either a full DN
    or just a CN works). Returns None if nothing matches."""
    best_role, best_idx = None, -1
    for dn in group_dns:
        dn_l = str(dn).lower()
        for key, role in group_role_map.items():
            if key and str(key).lower() in dn_l and role in ladder:
                idx = ladder.index(role)
                if idx > best_idx:
                    best_role, best_idx = role, idx
    return best_role


class ActiveDirectoryAuth:
    def __init__(self):
        self.mode = _auth_mode()
        # Surface obvious AD misconfiguration loudly at startup rather than letting
        # every login fail with a confusing error later.
        if self.mode == "ad":
            cfg = _ad_config()
            if not cfg["server"] or not cfg["domain"]:
                print("[auth] WARNING: AUTH_MODE=ad but the AD server/domain are not "
                      "configured. Set AD_SERVER/AD_DOMAIN (or AD_auth in config.json). "
                      "All logins will fail until this is fixed.")
            else:
                print(f"[auth] Active Directory mode enabled "
                      f"(server={cfg['server']}:{cfg['port']}, ssl={cfg['use_ssl']}, "
                      f"role_source={cfg['role_source']}).")

    def authenticate(self, credentials: HTTPBasicCredentials = Depends(security)):
        """Unified auth dependency used by every protected route.

        Dispatches to the configured backend. The return contract is identical
        for both backends: a [username, password] pair (the password is reused to
        open the caller's per-user Postgres connection).
        """
        if self.mode == "ad":
            return self.authenticate_user_ad(credentials)
        return self.authenticate_user_test(credentials)

    def authenticate_user_test(self, credentials: HTTPBasicCredentials = Depends(security)):
        """Mock backend (default). Verifies the password against config.json MOCK_AD."""
        username = credentials.username
        # Only AD people who have been onboarded into the system (inSystem) and
        # therefore carry a password may authenticate. AD-only candidates cannot.
        entry = configData["MOCK_AD"].get(username)
        if not entry or not entry.get("inSystem") or "password" not in entry:
            raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User with that username does not exists"
        )
        password = credentials.password
        if password == entry["password"]:
            return [username, password]
        else:
            raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Password does not match username"
        )

    def authenticate_user_ad(self, credentials: HTTPBasicCredentials = Depends(security)):
        """Real Active Directory backend.

        1. Verifies the credentials against the AD server with an LDAP SIMPLE bind.
        2. Requires the user to be onboarded into the system (an inSystem entry in
           the directory) so their employee_id / role / department can be resolved.
           Onboarding is managed via POST /employees; AD only owns the password,
           never the application role.
        """
        username = credentials.username
        password = credentials.password
        if not username or not password:
            # An empty password would otherwise trigger an LDAP "unauthenticated
            # bind", which succeeds without verifying anything.
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED,
                                detail="Username and password are required")

        cfg = _ad_config()
        if not cfg["server"] or not cfg["domain"]:
            # Misconfiguration, not a credential problem — make that distinguishable.
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                                detail="Active Directory is not configured")

        user_dn = f"{username}@{cfg['domain']}"
        bound = False
        derived_role = None
        try:
            server = ldap3.Server(cfg["server"], port=cfg["port"], use_ssl=cfg["use_ssl"],
                                  get_info=ldap3.NONE, connect_timeout=AD_CONNECT_TIMEOUT)
            conn = ldap3.Connection(server, user=user_dn, password=password,
                                    authentication="SIMPLE", receive_timeout=AD_CONNECT_TIMEOUT)
            bound = conn.bind()
            # While still bound, optionally read the user's groups to derive the role.
            if bound and cfg["role_source"] == "ad":
                derived_role = self._fetch_ad_role(conn, username, cfg)
            if bound:
                conn.unbind()
        except Exception as e:
            print(f"[auth] AD bind error for {username}: {e}")
            bound = False

        if not bound:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED,
                                detail="Invalid Active Directory credentials")

        # Credentials are valid in AD — but the person must also be a system
        # participant for us to resolve their role/department/employee_id.
        entry = configData["MOCK_AD"].get(username)
        if not entry or not entry.get("inSystem"):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN,
                                detail="User is not onboarded into the system")

        # Optional AD role mapping: a matched group overrides the directory role
        # for this login. If no group matches, the onboarding role is kept.
        if derived_role:
            entry["role"] = derived_role

        return [username, password]

    @staticmethod
    def _fetch_ad_role(conn, username, cfg):
        """Read the user's AD group membership and map it to an application role.
        Returns the mapped role, or None if no group matches / lookup is impossible."""
        base = cfg["search_base"] or _domain_to_base(cfg["domain"])
        if not base:
            return None
        search_filter = (f"(|(userPrincipalName={username}@{cfg['domain']})"
                         f"(sAMAccountName={username}))")
        if not conn.search(base, search_filter, attributes=["memberOf"]) or not conn.entries:
            return None
        try:
            member_of = conn.entries[0].memberOf
            groups = list(member_of.values) if member_of else []
        except Exception:
            groups = []
        ladder = configData.get("ROLE_LADDER", ["author", "executor", "manager", "top-manager"])
        return _map_groups_to_role(groups, cfg["group_role_map"], ladder)


class PgDbOperator:
    delegated_to_same_dep = configData["dep_configs"]["delegated_to_same_dep"]
    empl_appl_delay = configData["dep_configs"]["empl_appl_delay"]
    deadline_notification = configData["dep_configs"]["deadline_notification"]
    def __init__(self, user, password):
        try:
            #conn_info = "dbname=app_db user=" + user + " password=" + password
            conn_info = (
                f"dbname={os.getenv('DB_NAME')} "
                f"user={user} "
                f"password={password} "
                f"host={os.getenv('DB_HOST')} "
                f"port={os.getenv('DB_PORT')}"
            )
            self.pool = psycopg_pool.ConnectionPool(conninfo=conn_info, min_size=1, max_size= 10)
            atexit.register(self.pool.close)
            self.pool.wait()
            print("connection pool ready")
        except:
            print("cannot login with said data")
            print("try creating new role")

    def datetime_handler(self, obj):
        if isinstance(obj, datetime):
            return obj.isoformat() 
        raise TypeError("Unknown type")
    def WriteDataIntoJson(self, data, shouldWriteToFile = False):
        if shouldWriteToFile:
            with open('data.json', 'w', encoding="utf-8") as f:
                json.dump(data, fp = f, default=self.datetime_handler, ensure_ascii=False)
        return json.dumps(data, default=self.datetime_handler, ensure_ascii=False)
    
    def createUserRole(self, username, password, roleList):
        with self.pool.connection() as conn:
            exists = conn.execute(
                "SELECT 1 FROM pg_catalog.pg_roles WHERE rolname = %s",
                (username,)
            ).fetchone()
            if not exists:
                conn.execute(
                    pgsql.SQL("CREATE ROLE {} WITH LOGIN PASSWORD {}").format(
                        pgsql.Identifier(username),
                        pgsql.Literal(password)
                    )
                )
            else:
                # Keep the Postgres login password in sync with the supplied one.
                # In mock mode this re-sets the same value (harmless); in AD mode it
                # lets a rotated AD password take effect on the next fresh connection.
                conn.execute(
                    pgsql.SQL("ALTER ROLE {} WITH LOGIN PASSWORD {}").format(
                        pgsql.Identifier(username),
                        pgsql.Literal(password)
                    )
                )
            for role in roleList:
                try:
                    conn.execute(
                        pgsql.SQL("GRANT {} TO {}").format(
                            pgsql.Identifier(role.lower()),
                            pgsql.Identifier(username)
                        )
                    )
                except psycopg.errors.UndefinedObject:
                    conn.rollback()
                    print(f"Role {role} not found, skipping grant")
    def fillPermissionRoles(self, permissionList):
        for perm in permissionList:
            with self.pool.connection() as conn:
                try:
                    conn.execute("""
                        DO $$
                        BEGIN
                            IF NOT EXISTS (SELECT FROM pg_catalog.pg_roles WHERE rolname = '""" + perm.lower() + """') THEN
                                CREATE ROLE """ + perm + """ WITH NOLOGIN;
                            END IF;
                        END;
                        $$;
                    """)
                except psycopg.errors.DuplicateObject:
                    print(f"Permission role {perm} already exists, skipping.")
                    conn.rollback()

    def setupRoleTableGrants(self):
        """
        Create the two Postgres group roles that back the application's AD role
        model and grant them table privileges. Per-user membership in these roles
        is assigned in createUserRole(), derived from the user's AD role:

            app_table_base    every authenticated user — read everything, write
                              their own applications / delegations / notifications.
            app_table_manage  managers & top-managers — manage the directories
                              (departments, employees, work types and their grades).

        Department-level access (a manager only touching their own department) is
        enforced in the API layer, not via Postgres roles.
        """
        base_grants = [
            "GRANT USAGE ON SCHEMA public TO app_table_base",
            "GRANT SELECT ON ALL TABLES IN SCHEMA public TO app_table_base",
            "GRANT INSERT, UPDATE ON public.application TO app_table_base",
            "GRANT INSERT, DELETE ON public.employee_to_application TO app_table_base",
            "GRANT INSERT, UPDATE ON public.delegated TO app_table_base",
            "GRANT UPDATE ON public.notification TO app_table_base",
            "GRANT INSERT, UPDATE ON public.photo TO app_table_base",
            # Status-transition journal: every user records transitions for their
            # own application actions (written in the same tx as the state change).
            "GRANT INSERT ON public.application_status_history TO app_table_base",
        ]
        manage_grants = [
            "GRANT USAGE ON SCHEMA public TO app_table_manage",
            "GRANT INSERT, UPDATE, DELETE ON "
            "public.department, public.employee, public.types_of_works, "
            "public.type_of_work_to_grade TO app_table_manage",
            # Priority settings are persisted by a top-manager via PUT /priority-settings.
            "GRANT INSERT, UPDATE, DELETE ON public.priority_settings TO app_table_manage",
        ]
        # Create the group roles (idempotent).
        for role_name in ("app_table_base", "app_table_manage"):
            with self.pool.connection() as conn:
                conn.execute(
                    pgsql.SQL("""
                        DO $body$
                        BEGIN
                            IF NOT EXISTS (SELECT FROM pg_catalog.pg_roles WHERE rolname = {role_name}) THEN
                                CREATE ROLE {role_ident} WITH NOLOGIN;
                            END IF;
                        END;
                        $body$;
                    """).format(
                        role_name=pgsql.Literal(role_name),
                        role_ident=pgsql.Identifier(role_name),
                    )
                )
        # Re-apply grants on every startup (idempotent).
        with self.pool.connection() as conn:
            for stmt in base_grants + manage_grants:
                conn.execute(stmt)



    ######
    #
    #Insert command
    #

    def writeNewPost(self, name = "Должность", is_top = False):
        try:
            with self.pool.connection() as conn:
                conn.execute('INSERT INTO post (name, is_top' \
                                                            ") VALUES (%s,%s)", (name, is_top))
        except psycopg.errors.InsufficientPrivilege:
                conn.rollback()
                print("user dont have privilege for this command")
            
    def writeNewDepartment(self, name = "Отдел", group = "Основной", value = 0,  delegated_to_same_dep = delegated_to_same_dep, empl_appl_delay = empl_appl_delay, deadline_notification = deadline_notification):
        try:
            with self.pool.connection() as conn:
                conn.execute('INSERT INTO department ("group", value, name, ' \
                                                        "delegated_to_same_dep, empl_appl_delay, " \
                                                        "deadline_notification) VALUES (%s,%s,%s,%s,%s,%s)", (group, value, name, delegated_to_same_dep, empl_appl_delay, deadline_notification))
        except psycopg.errors.InsufficientPrivilege:
                conn.rollback()
                print("user dont have privilege for this command")  
                
    def tryWriteNewEmployee(self, fio = "Имя сотрудника", department_id = None, post_grade_id = None):
        now = datetime.now(project_timezone)
        with self.pool.connection() as conn:
            try:
                conn.execute('INSERT INTO employee (department_id, post_grade_id, fio, ' \
                                                        "created_at, updated_at " \
                                                        ") VALUES (%s,%s,%s,%s,%s)", (department_id, post_grade_id,  fio,now ,now ))
            except psycopg.errors.ForeignKeyViolation:
                conn.rollback()
                print("Error: Foreign key value does not exist for command tryWriteNewEmployee")
            except psycopg.errors.InsufficientPrivilege:
                conn.rollback()
                print("user dont have privilege for this command")

    def tryWriteNewTypeOfWork(self, name = "Вид работы", department_id = None, complexity_value = 0):
        with self.pool.connection() as conn:
            try:
                id = conn.execute('INSERT INTO types_of_works (name, complexity_value, department_id ' \
                                                        ") VALUES (%s,%s,%s) RETURNING type_of_works_id;", (name, complexity_value , department_id)).fetchall()
                return id
            except psycopg.errors.ForeignKeyViolation:
                conn.rollback()
                print("Error: Foreign key value does not exist for command tryWriteNewTypeOfWork")
                return "Error: Foreign key value does not exist for command tryWriteNewTypeOfWork"
               
            except psycopg.errors.InsufficientPrivilege:
                conn.rollback()
                print("user dont have privilege for this command")
                return "user dont have privilege for this command"

    def tryWriteNewPostGrade(self, post_id = None, grade_id = None):
        with self.pool.connection() as conn:
            try:
                conn.execute('INSERT INTO post_grade (post_post_id, grade_grade_id' \
                                                        ") VALUES (%s,%s)", (post_id, grade_id))
            except psycopg.errors.ForeignKeyViolation:
                conn.rollback()
                print("Error: Foreign key value does not exist for command tryWriteNewPostGrade")
            except psycopg.errors.InsufficientPrivilege:
                conn.rollback()
                print("user dont have privilege for this command")
    
    def tryWriteNewApplication(self, name = "Заявка1", priority_id:int = None, status_id:int =  None, description:str = "Без описания", delegated_id: int = None,
                                is_unfinished:bool = False, department_id:int = None ,types_of_works:int = None, empl_assigned_complexity:int = None,
                                is_expired:bool = False, deadline = None):
        now = datetime.now(project_timezone)
        with self.pool.connection() as conn:
            try:
                conn.execute('INSERT INTO application (name, priority_id, status_id, description, delegated_id, is_unfinished, department_id, types_of_works, empl_assigned_complexity, created_at, is_expired, deadline, updated_at' \
                                                        ") VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)", (name, priority_id, status_id, description, delegated_id, is_unfinished, department_id, types_of_works, empl_assigned_complexity, now, is_expired, deadline, now))
            except psycopg.errors.ForeignKeyViolation:
                conn.rollback()
                print("Error: Foreign key value does not exist for command tryWriteNewApplication")
            except psycopg.errors.InsufficientPrivilege:
                conn.rollback()
                print("user dont have privilege for this command")

    def tryWriteNewEmployeeToApplication(self, role_id:int = None, application_id:int = None, employee_id:int = None):
        with self.pool.connection() as conn:
            try:
                conn.execute('INSERT INTO employee_to_application (role_id, application_id, employee_id' \
                                                        ") VALUES (%s,%s, %s)", (role_id, application_id,employee_id ))
            except psycopg.errors.ForeignKeyViolation:
                conn.rollback()
                print("Error: Foreign key value does not exist for command tryWriteNewEmployeeToApplication")
            except psycopg.errors.InsufficientPrivilege:
                conn.rollback()
                print("user dont have privilege for this command")

    def writeNewComplexityValue(self, name = "Сложность"):
        try:
            with self.pool.connection() as conn:
                conn.execute('INSERT INTO complexity_value (name' \
                                                            ") VALUES (%s)", (name,))
        except psycopg.errors.InsufficientPrivilege:
            conn.rollback()
            print("user dont have privilege for this command")
    
    def writeNewStatus(self, name = "Состояние"):
        try:
         with self.pool.connection() as conn:
            conn.execute('INSERT INTO status (name' \
                                                        ") VALUES (%s)", (name,))
        except psycopg.errors.InsufficientPrivilege:
            conn.rollback()
            print("user dont have privilege for this command")

    def writeNewRole(self, name = "Состояние"):
        try:
         with self.pool.connection() as conn:
            conn.execute('INSERT INTO role (name' \
                                                        ") VALUES (%s)", (name,))
        except psycopg.errors.InsufficientPrivilege:
                conn.rollback()
                print("user dont have privilege for this command")

    def writeNewGrade(self, name = "Ранг"):
        try:
            with self.pool.connection() as conn:
                conn.execute('INSERT INTO grade (name' \
                                                            ") VALUES (%s)", (name,))
        except psycopg.errors.InsufficientPrivilege:
                conn.rollback()
                print("user dont have privilege for this command")
    def writeNewPriority(self, name = "Значение приоритета", value = 0):
        try:
         with self.pool.connection() as conn:
            conn.execute('INSERT INTO priority (name, value' \
                                                        ") VALUES (%s, %s)", (name, value))
        except psycopg.errors.InsufficientPrivilege:
                conn.rollback()
                print("user dont have privilege for this command")
    ### Осталось - фото, уведомления, delegated

    #
    #
    #Update command
    #
    #

    def updateSingleDataInTable(self, table:str, whereCon:str, column:str, newVal):
        try:
            with self.pool.connection() as conn:
                conn.execute('UPDATE ' + table  + ' SET ' + column + ' = ' + newVal + ' WHERE ' + whereCon )
        except psycopg.errors.InsufficientPrivilege:
                conn.rollback()
                print("user dont have privilege for this command")
        except:
            conn.rollback()
            print("cant update data in table" + table + " in column " + column)

    # 
    # Select command
    # 

    def getRowFromTable(self, table:str, identifierName:str, identifierValue, rowfactory = dict_row):
        try:
            requestString = 'SELECT *' 
            requestString += (" FROM " + table + " WHERE " + identifierName + " = " + str(identifierValue))
            
            with self.pool.connection() as conn:
                with conn.cursor(row_factory=rowfactory) as cur:
                    return cur.execute(requestString).fetchall()
        except:
           print("getting row error for table " + table + " with identifier" + identifierName)
           return None
    
    def getRowsFromTableWithJoin(self, table:str, joinStatement:str, identifierName, identifierValue, rowfactory = dict_row):
        data = self.getAllRowsFromTableWithJoin(table, joinStatement, rowfactory)
        filtered_data = [item for item in data if str(item[identifierName]) == identifierValue]
        return filtered_data
        
    def getAllRowsFromTableWithJoin(self, table:str, joinStatement:str, rowfactory = dict_row):

            requestString = 'SELECT * FROM ' + table + joinStatement
        
            with self.pool.connection() as conn:
                with conn.cursor(row_factory=rowfactory) as cur:
                    return cur.execute(requestString).fetchall()

        
    def getAllRowsFromTable(self, table:str, rowfactory = dict_row):
        try:
            requestString = 'SELECT * FROM ' + table
            
            with self.pool.connection() as conn:
                 with conn.cursor(row_factory=rowfactory) as cur:
                    return cur.execute(requestString).fetchall()
        except:
           print("getting all rows error for table " + table)
           return None


    def getColumnFromTable(self, table:str, column:str, limit:int = None, orderbyDesc:str = None, orderbyAsc:str = None, whereCon:str = None, rowfactory = dict_row):
       try:
            requestString = 'SELECT ' + column
            requestString += (" FROM " + table )
            if type(orderbyAsc) == str:
                requestString += " ORDER BY " + orderbyAsc +" ASC"
            if type(orderbyDesc) == str:
                requestString += " ORDER BY " + orderbyDesc +" DESC"
            if type(whereCon) == str:
                requestString += " WHERE " + whereCon
            if type(limit) == int:
                requestString += " LIMIT " + str(abs(limit))
            with self.pool.connection() as conn:
                with conn.cursor(row_factory=rowfactory) as cur:
                    return cur.execute(requestString).fetchall()
       except:
           print("getting column error for table " + table + " in column " + column)
           return None
    def getColumnsFromTable(self, table:str, columns:list[str], limit:int = None, orderbyDesc:str = None, orderbyAsc:str = None, whereCon:str = None, rowfactory = dict_row):
        try:
            requestString = 'SELECT '
            for column in columns:
                requestString += (column +", ")
            requestString = requestString[:-2]
            requestString += (" FROM " + table )
            if type(orderbyAsc) == str:
                requestString += " ORDER BY " + orderbyAsc +" ASC"
            if type(orderbyDesc) == str:
                requestString += " ORDER BY " + orderbyDesc +" DESC"
            if type(whereCon) == str:
                requestString += " WHERE " + whereCon
            if type(limit) == int:
                requestString += " LIMIT " + str(abs(limit))
            with self.pool.connection() as conn:
                with conn.cursor(row_factory=rowfactory) as cur:
                    return cur.execute(requestString).fetchall()
        except:
            print("getting columns error for table " + table + " with columns ")
            for column in columns:
                print(column)
            return None
        
    #######
    ##
    #Delete and truncate command
    #
    #
    #
    def deleteAllDataFromAllTables(self):
        try:
            with self.pool.connection() as conn:
                conn.execute("DO $$ DECLARE r RECORD; BEGIN FOR r IN (SELECT tablename FROM pg_tables WHERE schemaname = 'public') LOOP EXECUTE 'TRUNCATE TABLE public.' || quote_ident(r.tablename) || ' RESTART IDENTITY CASCADE;';END LOOP;END $$;")
            print("cleared all tables")
        except:
            print("cant delete data from all tables")

    def deleteDataFromTable(self, table:str, whereCon:str):
        try:
            with self.pool.connection() as conn:
                conn.execute("DELETE FROM " + table +" WHERE " + whereCon)
            print("cleared data from " + table + " where " + whereCon)
        except:
            print("cant delete data from " +table +" with condition: " + whereCon)

    def deleteAllDataFromTable(self, table:str):
        try:
            with self.pool.connection() as conn:
                conn.execute("TRUNCATE TABLE " + table)
            print("cleared data from " + table)
        except:
            print("cant truncate table:" +table)

    def deleteAllDataFromTableCascade(self, table:str):
        try:
            with self.pool.connection() as conn:
                conn.execute("TRUNCATE TABLE " + table +" RESTART IDENTITY CASCADE")
        except:
            print("cant truncate table:" +table)

            

def convertPhotoToBase64(photo):
    try:
        with open(Path(__file__).parent.parent / photo, "rb") as image_file:
            encoded_string = base64.b64encode(image_file.read()).decode('utf-8')
            return encoded_string
    except:
        print("cant convert photo")
        return None

def convertBase64ToPhoto(bytePhoto, shouldWriteToFile = False, WriteToDirectory = (Path(__file__).parent.parent), NameToWrite = "output_image.png"):
    try:
        image_data = base64.b64decode(bytePhoto)
        if shouldWriteToFile:
            with open(WriteToDirectory/NameToWrite, "wb") as f:
                f.write(image_data)
        return image_data
    except:
        print("cant convert photo from this string")
        return None




