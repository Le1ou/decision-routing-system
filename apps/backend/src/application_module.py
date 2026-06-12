"""
application_module.py — базовый слой backend: загрузка config.json, аутентификация
(mock-каталог / Active Directory) и обёртка подключения к Postgres (PgDbOperator).

При рефакторинге из класса PgDbOperator удалён мёртвый CRUD-слой (writeNew*/tryWrite*/
getColumn*/delete* и конвертеры фото) — он нигде не вызывался; вся работа с данными
давно идёт параметризованным SQL в api-модулях и подсистемах.
"""

import atexit
import json
import os
from datetime import timezone
from pathlib import Path

import ldap3
import psycopg
import psycopg_pool
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from psycopg import sql as pgsql
from psycopg.rows import dict_row

configPath = Path(__file__).parent.parent / "config.json"
project_timezone = timezone.utc
with configPath.open(encoding="utf-8") as config_data:
    configData = json.load(config_data)

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
    """Пул подключений к Postgres от имени конкретного пользователя (login-роли).

    Системный экземпляр (postgres) создаётся в core.py; per-user экземпляры кэшируются
    в core.get_db_user. Помимо пула, объект отвечает за создание login-ролей и выдачу
    табличных грантов групповым ролям.
    """

    def __init__(self, user, password):
        try:
            conn_info = (
                f"dbname={os.getenv('DB_NAME')} "
                f"user={user} "
                f"password={password} "
                f"host={os.getenv('DB_HOST')} "
                f"port={os.getenv('DB_PORT')}"
            )
            self.pool = psycopg_pool.ConnectionPool(conninfo=conn_info, min_size=1, max_size=10)
            atexit.register(self.pool.close)
            self.pool.wait()
            print("connection pool ready")
        except Exception:
            print("cannot login with said data")
            print("try creating new role")

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
                            # Раньше имя роли подставлялось без кавычек и Postgres сводил его
                            # к нижнему регистру — сохраняем то же имя (canmanageemployees…).
                            role_name=pgsql.Literal(perm.lower()),
                            role_ident=pgsql.Identifier(perm.lower()),
                        )
                    )
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
            # Чат заявки: писать сообщения и обновлять свой маркер прочитанности.
            "GRANT INSERT ON public.application_message TO app_table_base",
            "GRANT INSERT, UPDATE ON public.application_chat_read TO app_table_base",
        ]
        manage_grants = [
            "GRANT USAGE ON SCHEMA public TO app_table_manage",
            "GRANT INSERT, UPDATE, DELETE ON "
            "public.department, public.employee, public.types_of_works, "
            "public.type_of_work_to_grade, public.type_of_work_to_post TO app_table_manage",
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

    # ── Select helpers ──

    def getRowFromTable(self, table: str, identifierName: str, identifierValue, rowfactory=dict_row):
        """Все строки таблицы, где столбец равен значению (значение — параметром,
        а не конкатенацией строк, как раньше). Имена таблицы/столбца — только
        внутренние константы кода, никогда не пользовательский ввод."""
        try:
            query = pgsql.SQL("SELECT * FROM {} WHERE {} = %s").format(
                pgsql.Identifier(table), pgsql.Identifier(identifierName)
            )
            with self.pool.connection() as conn:
                with conn.cursor(row_factory=rowfactory) as cur:
                    return cur.execute(query, (identifierValue,)).fetchall()
        except Exception as e:
            print(f"getting row error for table {table} with identifier {identifierName}: {e}")
            return None

    def getAllRowsFromTable(self, table: str, rowfactory=dict_row):
        try:
            query = pgsql.SQL("SELECT * FROM {}").format(pgsql.Identifier(table))
            with self.pool.connection() as conn:
                with conn.cursor(row_factory=rowfactory) as cur:
                    return cur.execute(query).fetchall()
        except Exception as e:
            print(f"getting all rows error for table {table}: {e}")
            return None
