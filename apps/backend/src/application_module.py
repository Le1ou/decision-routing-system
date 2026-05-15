import psycopg
import  psycopg_pool
import atexit
import json
from pathlib import Path
from datetime import datetime, timezone
import base64
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

    def writeNewPost(self, name = "Отдел", is_top = False):
         with self.pool.connection() as conn:
            conn.execute('INSERT INTO post (name, is_top' \
                                                        ") VALUES (%s,%s)", (name, is_top))
            
    def writeNewDepartment(self, name = "Отдел", group = "Основной", value = 0,  delegated_to_same_dep = delegated_to_same_dep, empl_appl_delay = empl_appl_delay, deadline_notification = deadline_notification):
         with self.pool.connection() as conn:
            conn.execute('INSERT INTO department ("group", value, name, ' \
                                                        "delegated_to_same_dep, empl_appl_delay, " \
                                                        "deadline_notification) VALUES (%s,%s,%s,%s,%s,%s)", (group, value, name, delegated_to_same_dep, empl_appl_delay, deadline_notification))
            
    def tryWriteNewEmployee(self, fio = "Имя сотрудника", department_id = -1, post_id = -1):
        now = datetime.now(project_timezone)
        with self.pool.connection() as conn:
            try:
                conn.execute('INSERT INTO employee (post_id, department_id, fio, ' \
                                                        "created_at, updated_at " \
                                                        ") VALUES (%s,%s,%s,%s,%s)", (post_id, department_id, fio,now ,now ))
            except psycopg.errors.ForeignKeyViolation:
                conn.rollback()
                print("Error: Foreign key value does not exist.")

    def tryWriteNewTypeOfWork(self, name = "Вид работы", department_id = -1, complexity_value = 0):
        with self.pool.connection() as conn:
            try:
                conn.execute('INSERT INTO types_of_work (name, complexity_value, department_id, ' \
                                                        ") VALUES (%s,%s,%s)", (name, complexity_value , department_id))
            except psycopg.errors.ForeignKeyViolation:
                conn.rollback()
                print("Error: Foreign key value does not exist.")

    def writeNewComplexityValue(self, name = "Сложность"):
         with self.pool.connection() as conn:
            conn.execute('INSERT INTO complexity_value (name' \
                                                        ") VALUES (%s)", (name))
    def writeNewStatus(self, name = "Состояние"):
         with self.pool.connection() as conn:
            conn.execute('INSERT INTO status (name' \
                                                        ") VALUES (%s)", (name))
    def writeNewRole(self, name = "Состояние"):
         with self.pool.connection() as conn:
            conn.execute('INSERT INTO role (name' \
                                                        ") VALUES (%s)", (name))
    def writeNewGrade(self, name = "Ранг"):
         with self.pool.connection() as conn:
            conn.execute('INSERT INTO grade (name' \
                                                        ") VALUES (%s)", (name))
    def writeNewPriority(self, name = "Значение приоритета", value = 0):
         with self.pool.connection() as conn:
            conn.execute('INSERT INTO priority (name, value' \
                                                        ") VALUES (%s, %s)", (name, value))
    ### Осталось - фото, many-to-many таблицы, уведомления, !!!! Заявка!!!!.   
    # 
    # 
    # 
    def getColumnFromTable(self, table:str, column:str, limit:int = None, orderbyDesc:str = None, orderbyAsc:str = None, whereCon:str = None):
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
                return conn.execute(requestString).fetchall()
       except:
           print("getting column error for table " + table + " in column " + column)
           return None
    def getColumnsFromTable(self, table:str, columns:list[str], limit:int = None, orderbyDesc:str = None, orderbyAsc:str = None, whereCon:str = None):
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
                return  conn.execute(requestString).fetchall()
        except:
            print("getting columns error for table " + table + " with columns ")
            for column in columns:
                print(column)
            return None



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
#test.writeNewDepartment("Отдел безопасности3", "основное отделение", 1, True, 10)
print(test.getColumnFromTable( "department","name", ))
print(test.getColumnFromTable( "department",'"group"'))
print("-----------------------------------------")
print(test.getColumnsFromTable("department", ["name", 'department_id'], limit = 3, whereCon="department_id = '1'"))
#test.writeNewPost(name = "Сотрудник", is_top = False)
#test.tryWriteNewEmployee("Иванов Иван Иванович", 1, 1)
#test.tryWriteNewTypeOfWork("Починить комп", 1, 1)
