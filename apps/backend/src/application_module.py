import psycopg
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

configPath = Path(__file__).parent.parent / "config.json"
global configData
project_timezone = timezone.utc
with configPath.open() as config_data:
    configData = json.load(config_data)
    config_data.close()

security = HTTPBasic()
class ActiveDirectoryAuth:
    def __init__(self):
        pass
    def authenticate_user_test(self, credentials: HTTPBasicCredentials = Depends(security)):
        username = credentials.username
        if username not in configData["MOCK_USERS_DB"]:
            raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, 
            detail="User with that username does not exists"
        )
        password = credentials.password
        stored_password = configData["MOCK_USERS_DB"][username]["password"]
        if password == stored_password:
            return [username, password]
        else:
            raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, 
            detail="Password does not match username"
        )
            
    def authenticate_user(self, username, password):
        # Формируем имя пользователя для LDAP (формат UPN или sAMAccountName)
        user_dn = f'{username}@{config_data["AD_auth"]["Domain"]}'
        
        server = ldap3.Server(config_data["AD_auth"]["server_adress"], get_info=ldap3.ALL)
        try:
            # Подключаемся к серверу
            conn = ldap3.Connection(server, user=user_dn, password=password, authentication='SIMPLE')
            
            # Пытаемся привязаться (bind) к серверу
            if conn.bind():
                print("Успешная авторизация!")
                # conn.entries содержит информацию о пользователе из AD
                conn.unbind()
                return True
        except Exception as e:
            print(f"Ошибка авторизации: {e}")
        

class PgDbOperator:
    delegated_to_same_dep = configData["dep_configs"]["delegated_to_same_dep"]
    empl_appl_delay = configData["dep_configs"]["empl_appl_delay"]
    deadline_notification = configData["dep_configs"]["deadline_notification"]
    def __init__(self, user, password):
        try:
            conn_info = "dbname=app_db user=" + user + " password=" + password
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
                # try:
                    conn.execute("""
                                            DO $$
                                            BEGIN
                                                IF NOT EXISTS (SELECT FROM pg_catalog.pg_roles WHERE rolname = '"""+ username+ """') THEN
                                                    CREATE ROLE """+ username+ """ WITH LOGIN PASSWORD '""" + password +"""';
                                                END IF;
                                            END;
                                            $$;
                                        """
                                )
                    for role in roleList:
                        conn.execute(
                          "GRANT " + role + " TO " + username
                        )
                    
                # except :
                #     print("ошибка создания пользователя")
                #     conn.rollback() 
    def fillDbRolesBasedOnADTest(self, roleList):
        for role in roleList:
            with self.pool.connection() as conn:
                try:
                    conn.execute("""
                                            DO $$
                                            BEGIN
                                                IF NOT EXISTS (SELECT FROM pg_catalog.pg_roles WHERE rolname = '"""+ role+ """') THEN
                                                    CREATE ROLE """+ role+ """ WITH NOLOGIN;
                                                    GRANT USAGE ON SCHEMA public TO """+ role+ """;
                                                    GRANT INSERT, UPDATE ON public.application TO """+ role+ """;
                                                    GRANT SELECT ON ALL TABLES IN SCHEMA public TO """+ role+ """;
                                                END IF;
                                            END;
                                            $$;
                                        """
                                )
                    if "lead" in role:
                        conn.execute("GRANT UPDATE ON public.department, public.employee, public.types_of_works TO "+ role )
                except psycopg.errors.DuplicateObject:
                    print("Роль уже существует, пропускаем создание.")
                    conn.rollback() 
               


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




