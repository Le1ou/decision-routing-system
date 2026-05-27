from apps.backend.src.application_module import *
from fastapi import FastAPI, Depends, Body
from pydantic import BaseModel, RootModel, field_validator, Field, BeforeValidator
from typing import Literal
from typing import Annotated
#
#Workflow системы:
#Засетапить управленческую учетку для ДБ, объект аутентификации и сервер
DBController = PgDbOperator("postgres", "postgres") #Учетка с полным доступом к БД
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
#DBController.writeNewComplexityValue("легко")
# DBController.tryWriteNewTypeOfWork("Починить комп", 1, 1)
#DBController.deleteAllDataFromAllTables()
#print(DBController.getAllRowsFromTable("types_of_works"))
#print(DBController.getAllRowsFromTableWithJoin("types_of_works", " LEFT JOIN public.type_of_work_to_post_grade ON types_of_works.type_of_works_id = type_of_work_to_post_grade.type_of_works_id;"))

ComplexityValues = ["easy", "medium", "hard", "critical"]
def validateDataAndType(data, acceptedType, dataName = "data", cantBeEmpty = True, onlyAcceptedValues = []):
    try:
        acceptedType(data)
    except:
        raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, 
                detail= dataName + " is not in correct format")
    if cantBeEmpty:
        if str(data) == "":
            raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, 
                    detail=dataName + " is empty")
    if len(onlyAcceptedValues ) > 0:
        if not data in onlyAcceptedValues:
            raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, 
                    detail=dataName + " is not a correct value")

def coerce_to_str(v) -> str:
    return str(v) if v is not None else v

def coerce_to_str_multiple(v):
    if isinstance(v, list):
        return [str(item) for item in v]
    return v

# Создаем переиспользуемый тип с автоконвертацией
CoercedStr = Annotated[str, BeforeValidator(coerce_to_str)]
ListOfStrings = Annotated[list[str], BeforeValidator(coerce_to_str_multiple)]
class TypeOfWorks(BaseModel):
    name: str
    departmentId: int
    complexity:str

class TypeOfWorksReturn(BaseModel):
    id: CoercedStr = Field(validation_alias = 'type_of_works_id')
    name: CoercedStr = Field(validation_alias = 'name')
    departmentId:CoercedStr = Field(validation_alias = 'department_id')
    complexity: Literal["easy", "medium", "hard", "critical"] = Field(validation_alias='complexity_value')
    @field_validator('complexity', mode='before')
    @classmethod
    def convert_index_to_string(cls, value):
        # Если пришло число (индекс)
        if isinstance(value, int):
            # Проверяем, входит ли индекс в границы списка
            if 0 <= value < len(ComplexityValues):
                return ComplexityValues[value]
            else:
                raise ValueError(f"Индекс сложности {value} вне диапазона (должен быть от 0 до {len(ComplexityValues)-1})")
        
        # Если уже пришла строка, возвращаем как есть (на случай, если данные уже чистые)
        return value
    allowedPositionIds: ListOfStrings = Field(validation_alias = 'post_grade_ids')

class TypeOfWorksList(RootModel[list[TypeOfWorksReturn]]):
    pass
join_str =  """
    LEFT JOIN (
        SELECT 
            type_of_works_id, 
            COALESCE(json_agg(post_grade_id) FILTER (WHERE post_grade_id IS NOT NULL), '[]'::json) as post_grade_ids
        FROM public.type_of_work_to_post_grade
        GROUP BY type_of_works_id
    ) type_of_work_to_post_grade ON type_of_work_to_post_grade.type_of_works_id = type_of_work_to_post_grade.type_of_works_id;
    """
data = DBController.getAllRowsFromTableWithJoin("types_of_works", join_str)
collection = TypeOfWorksList.model_validate(data)

new_list = collection.model_dump()
print(new_list)
@app.get("/work-types")
def get_types_of_work(userData = Depends(authObj.authenticate_user_test)):
    try:
        DBController.createUserRole(userData[0],userData[1], configData["MOCK_USERS_DB"][userData[0]]["roles"] ) # Создаем учетку если она не существует
        DBUser = PgDbOperator(userData[0], userData[1])

        join_str =  """
            LEFT JOIN (
                SELECT 
                    type_of_works_id, 
                    COALESCE(json_agg(post_grade_id) FILTER (WHERE post_grade_id IS NOT NULL), '[]'::json) as post_grade_ids
                FROM public.type_of_work_to_post_grade
                GROUP BY type_of_works_id
            ) type_of_work_to_post_grade ON types_of_works.type_of_works_id = type_of_work_to_post_grade.type_of_works_id;
            """
        data = DBUser.getAllRowsFromTableWithJoin("types_of_works", join_str)
        collection = TypeOfWorksList.model_validate(data)

        new_list = collection.model_dump()
        print(new_list)
        if data == [] or data == None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, 
                detail="no data")
        else:
            
            return new_list
    except Exception as exc: 
        if hasattr(exc, "status_code")  and hasattr(exc, "detail"):
            raise HTTPException(
                status_code=exc.status_code, 
                detail=exc.detail)
        else:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, 
                detail="Server-side error")
        
@app.post("/work-types", status_code=201)
#def set_types_of_work(userData = Depends(authObj.authenticate_user_test), name:str| None = Body(default=""), departmentId:int| None = Body(default=0), complexity_value:int | None = Body(default=int(0))):
def set_types_of_work(typeOfWorks:TypeOfWorks, userData = Depends(authObj.authenticate_user_test)):
    try:
        validateDataAndType(typeOfWorks.name, str,"Наименование вида работ", False)
        validateDataAndType(typeOfWorks.departmentId, int,"ID департамента", False)
        validateDataAndType(typeOfWorks.complexity, str,"Сложность", False, ComplexityValues)
        DBController.createUserRole(userData[0],userData[1], configData["MOCK_USERS_DB"][userData[0]]["roles"] ) # Создаем учетку если она не существует
        DBUser = PgDbOperator(userData[0], userData[1])
        data = DBController.tryWriteNewTypeOfWork(typeOfWorks.name,int(typeOfWorks.departmentId),ComplexityValues.index(typeOfWorks.complexity))
        if data == [] or data == None or data == "":
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN, 
                detail="Cant write data with your rights")
        else:
            try: 
                return int(data[0][0])
            except:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, 
                    detail=data)

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
