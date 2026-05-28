from src.application_module import *
from fastapi import FastAPI, Depends, Body
import os
#
#Workflow системы:
#Засетапить управленческую учетку для ДБ, объект аутентификации и сервер
#DBController = PgDbOperator("postgres", "postgres") #Учетка с полным доступом к БД
DBController = PgDbOperator(
    os.getenv("DB_USER"),
    os.getenv("DB_PASSWORD")
)
DBController.fillDbRolesBasedOnADTest(configData["ROLES"]) # Создаем все роли, чтобы они точно были. Выдаем базовые права. Остальное выдается вручную
authObj = ActiveDirectoryAuth() #Я хз зачем я это отдельным объектом сделал, мб потом пригодится
app = FastAPI()

#Разрешаем доступ к серверу
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
#Выполнять команды пользователя по запросам, для каждого запроса проводить заново аутентификацию и создание своего управляющего БД объекта
# DBController.writeNewDepartment("Отдел безопасности", "основное отделение", 1, True, 10)
# DBController.writeNewComplexityValue("легко")
# DBController.tryWriteNewTypeOfWork("Починить комп", 1, 1)
# DBController.deleteAllDataFromAllTables()

@app.get("/health")
def health():
    return {"status": "ok"}

@app.get("/types_of_works")
def get_types_of_work(userData = Depends(authObj.authenticate_user_test)):
    try:
        DBController.createUserRole(userData[0],userData[1], configData["MOCK_USERS_DB"][userData[0]]["roles"] ) # Создаем учетку если она не существует
        DBUser = PgDbOperator(userData[0], userData[1])


        data = DBUser.getAllRowsFromTable("types_of_works")


        if data == [] or data == None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, 
                detail="no data")
        else:
            return data
    except Exception as exc: 
        if hasattr(exc, "status_code")  and hasattr(exc, "detail"):
            raise HTTPException(
                status_code=exc.status_code, 
                detail=exc.detail)
        else:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, 
                detail="Server-side error")
        
@app.post("/types_of_works")
def set_types_of_work(userData = Depends(authObj.authenticate_user_test), name:str| None = Body(default=""), department_id:int| None = Body(default=0), complexity_value:int | None = Body(default=int(0))):
    try:
        if name == "":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, 
                detail="name is not set")
        if department_id == 0:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, 
                detail="department id is not set")
        DBController.createUserRole(userData[0],userData[1], configData["MOCK_USERS_DB"][userData[0]]["roles"] ) # Создаем учетку если она не существует
        DBUser = PgDbOperator(userData[0], userData[1])


        data = DBController.tryWriteNewTypeOfWork(name,department_id,complexity_value)
        

        if data == [] or data == None or data == "":
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN, 
                detail="Cant write data with your rights")
        elif data != True:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, 
                detail=data)
        else:
            return data
    except Exception as exc: 
        if hasattr(exc, "status_code")  and hasattr(exc, "detail"):
            raise HTTPException(
                status_code=exc.status_code, 
                detail=exc.detail)
        else:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, 
                detail="Server-side error")


#print(convertPhotoToBase64("test_img.jpg"))
#print(convertBase64ToPhoto(convertPhotoToBase64("test_img.jpg")))

#
#superTest.fillDbRolesBasedOnADTest(configData["ROLES"])
#superTest.createUserRole("ivanov_i","SecretPassword!1", configData["MOCK_USERS_DB"]["ivanov_i"]["roles"] )
#test = PgDbOperator("postgres", "postgres")
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

# test.deleteAllDataFromAllTables()
# test.writeNewPost("Уборщик", False)
# test.writeNewPost("Начальник", False)
# test.writeNewGrade("низший")
# test.writeNewGrade("высший")
# test.tryWriteNewPostGrade(1,1)
# test.tryWriteNewPostGrade(1,2)
# test.tryWriteNewPostGrade(2,1)
# test.tryWriteNewPostGrade(2,2)
# test.writeNewDepartment("Отдел безопасности", "основное отделение", 1, True, 10)
# test.tryWriteNewEmployee("Иванов Иван Иванович", 1, 1)
# test.tryWriteNewEmployee("Петров Петр Петрович", 1, 3)
# print(test.getColumnFromTable("post_grade", "post_grade_id"))
# print(test.getColumnFromTable("department", "department_id"))
# print(test.getAllRowsFromTable("employee"))
# print((test.getColumnFromTable("employee", "created_at", limit= 1)))
# test.tryWriteNewApplication()
# print(test.WriteDataIntoJson(test.getAllRowsFromTable("employee")))
# test.deleteAllDataFromAllTables()

#test.tryWriteNewTypeOfWork("Починить комп", 1, 1)
