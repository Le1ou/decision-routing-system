import psycopg
import  psycopg_pool
import atexit
import json
from pathlib import Path
from datetime import datetime, timezone
import base64
from psycopg.rows import dict_row
configPath = Path(__file__).parent.parent / "config.json"
global configData
project_timezone = timezone.utc
with configPath.open() as config_data:
    configData = json.load(config_data)
    config_data.close()

class PgDbOperator:
    delegated_to_same_dep = configData["dep_configs"]["delegated_to_same_dep"]
    empl_appl_delay = configData["dep_configs"]["empl_appl_delay"]
    deadline_notification = configData["dep_configs"]["deadline_notification"]
    def __init__(self):
        conn_info = "dbname=app_db user=postgres password=postgres"
        self.pool = psycopg_pool.ConnectionPool(conninfo=conn_info, min_size=1, max_size= 10)
        atexit.register(self.pool.close)
        self.pool.wait()
        print("connection pool ready")

    def datetime_handler(self, obj):
        if isinstance(obj, datetime):
            return obj.isoformat() 
        raise TypeError("Unknown type")
    def WriteDataIntoJson(self, data):
        with open('data.json', 'w', encoding="utf-8") as f:
            json.dump(data, fp = f, default=self.datetime_handler, ensure_ascii=False)
        return json.dumps(data, default=self.datetime_handler, ensure_ascii=False)

    ######
    #
    #Insert command
    #

    def writeNewPost(self, name = "Должность", is_top = False):
         with self.pool.connection() as conn:
            conn.execute('INSERT INTO post (name, is_top' \
                                                        ") VALUES (%s,%s)", (name, is_top))
            
    def writeNewDepartment(self, name = "Отдел", group = "Основной", value = 0,  delegated_to_same_dep = delegated_to_same_dep, empl_appl_delay = empl_appl_delay, deadline_notification = deadline_notification):
         with self.pool.connection() as conn:
            conn.execute('INSERT INTO department ("group", value, name, ' \
                                                        "delegated_to_same_dep, empl_appl_delay, " \
                                                        "deadline_notification) VALUES (%s,%s,%s,%s,%s,%s)", (group, value, name, delegated_to_same_dep, empl_appl_delay, deadline_notification))
            
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

    def tryWriteNewTypeOfWork(self, name = "Вид работы", department_id = None, complexity_value = 0):
        with self.pool.connection() as conn:
            try:
                conn.execute('INSERT INTO types_of_work (name, complexity_value, department_id, ' \
                                                        ") VALUES (%s,%s,%s)", (name, complexity_value , department_id))
            except psycopg.errors.ForeignKeyViolation:
                conn.rollback()
                print("Error: Foreign key value does not exist for command tryWriteNewTypeOfWork")

    def tryWriteNewPostGrade(self, post_id = None, grade_id = None):
        with self.pool.connection() as conn:
            try:
                conn.execute('INSERT INTO post_grade (post_post_id, grade_grade_id' \
                                                        ") VALUES (%s,%s)", (post_id, grade_id))
            except psycopg.errors.ForeignKeyViolation:
                conn.rollback()
                print("Error: Foreign key value does not exist for command tryWriteNewPostGrade")
    
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

    def tryWriteNewEmployeeToApplication(self, role_id:int = None, application_id:int = None, employee_id:int = None):
        with self.pool.connection() as conn:
            try:
                conn.execute('INSERT INTO employee_to_application (role_id, application_id, employee_id' \
                                                        ") VALUES (%s,%s, %s)", (role_id, application_id,employee_id ))
            except psycopg.errors.ForeignKeyViolation:
                conn.rollback()
                print("Error: Foreign key value does not exist for command tryWriteNewEmployeeToApplication")

    def writeNewComplexityValue(self, name = "Сложность"):
         with self.pool.connection() as conn:
            conn.execute('INSERT INTO complexity_value (name' \
                                                        ") VALUES (%s)", (name,))
    def writeNewStatus(self, name = "Состояние"):
         with self.pool.connection() as conn:
            conn.execute('INSERT INTO status (name' \
                                                        ") VALUES (%s)", (name,))
    def writeNewRole(self, name = "Состояние"):
         with self.pool.connection() as conn:
            conn.execute('INSERT INTO role (name' \
                                                        ") VALUES (%s)", (name,))
    def writeNewGrade(self, name = "Ранг"):
         with self.pool.connection() as conn:
            conn.execute('INSERT INTO grade (name' \
                                                        ") VALUES (%s)", (name,))
    def writeNewPriority(self, name = "Значение приоритета", value = 0):
         with self.pool.connection() as conn:
            conn.execute('INSERT INTO priority (name, value' \
                                                        ") VALUES (%s, %s)", (name, value))
    ### Осталось - фото, уведомления, delegated

    #
    #
    #Update command
    #
    #

    def updateSingleDataInTable(self, table:str, whereCon:str, column:str, newVal):
        with self.pool.connection() as conn:
            conn.execute('UPDATE ' + table  + ' SET ' + column + ' = ' + newVal + ' WHERE ' + whereCon )

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









#print(convertPhotoToBase64("test_img.jpg"))
#print(convertBase64ToPhoto(convertPhotoToBase64("test_img.jpg")))


test = PgDbOperator()
#print(datetime.now(project_timezone))
#test.writeNewDepartment("Отдел безопасности", "основное отделение", 1, True, 10)
#test.deleteDataFromTable("department", "department_id = 1")
#test.deleteAllDataFromTableCascade("department")
#print(test.getColumnFromTable( "department","name" ))
#test.updateSingleDataInTable("department", "department_id = 1", "name" , "'Новый отдел'")
#print(test.getColumnFromTable( "department","name" ))
#print(test.getColumnFromTable( "department",'"group"'))
#print("-----------------------------------------")
#print(test.getColumnsFromTable("department", ["name", 'department_id'], limit = 3, whereCon="department_id = '1'"))
#test.writeNewPost(name = "Сотрудник", is_top = False)

test.deleteAllDataFromAllTables()
test.writeNewPost("Уборщик", False)
test.writeNewPost("Начальник", False)
test.writeNewGrade("низший")
test.writeNewGrade("высший")
test.tryWriteNewPostGrade(1,1)
test.tryWriteNewPostGrade(1,2)
test.tryWriteNewPostGrade(2,1)
test.tryWriteNewPostGrade(2,2)
test.writeNewDepartment("Отдел безопасности", "основное отделение", 1, True, 10)
test.tryWriteNewEmployee("Иванов Иван Иванович", 1, 1)
test.tryWriteNewEmployee("Петров Петр Петрович", 1, 3)
print(test.getColumnFromTable("post_grade", "post_grade_id"))
print(test.getColumnFromTable("department", "department_id"))
print(test.getAllRowsFromTable("employee"))
print((test.getColumnFromTable("employee", "created_at", limit= 1)))
test.tryWriteNewApplication()
print(test.WriteDataIntoJson(test.getAllRowsFromTable("employee")))
test.deleteAllDataFromAllTables()

#test.tryWriteNewTypeOfWork("Починить комп", 1, 1)
