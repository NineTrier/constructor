from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException
from selenium.webdriver.chrome.options import Options
import time
import requests
import pandas as pd
import os
import re
import datetime
import logging
import json

def authorize_on_site(driver):
    try:
        global login, password
        driver.get(f'{url}/accounts/login')

        form = WebDriverWait(driver, 10).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, 'form'))
        )

        # Заполните логин и пароль
        username_input = form.find_element(By.NAME, 'username')
        username_input.send_keys(login)

        password_input = form.find_element(By.NAME, 'password')
        password_input.send_keys(password)

        # Нажмите кнопку входа
        submit_button = form.find_element(By.CSS_SELECTOR, 'button[type="submit"]')
        submit_button.click()
        logging.info('Авторизация на сайте прошла успешно')
    except Exception as e:
        logging.error(f'Ошибка при авторизации на сайте: {str(e)}')
        
    
def go_to_document(driver, id):
    driver.get(f'{url}/document/view?id={id}&type=0')
    identificator_input = WebDriverWait(driver, 10).until(
        EC.presence_of_element_located((By.CSS_SELECTOR, 'input.identificator.obj_parameter_value'))
    )

def load_data_from_identificator(driver, object_id, ident_text):
    identificator_input = driver.find_element(By.CSS_SELECTOR, f'#{object_id} input.identificator.obj_parameter_value')
    # Нажать на элемент input с классом "identificator"
    driver.execute_script("arguments[0].click()", identificator_input)

    # Дождаться появления модального окна
    modal_window = WebDriverWait(driver, 10).until(
        EC.presence_of_element_located((By.ID, 'objectFindDataModal'))
    )
    
    print('##### Модальное окно открыто')

    # Найти элемент с классом "ident" и текстом "А31-11766/2024"
    ident_element = WebDriverWait(driver, 10).until(
        EC.presence_of_element_located((By.CSS_SELECTOR, ".ident"))
    )
    ident_elements = driver.find_elements(By.CSS_SELECTOR, ".ident")
    for element in ident_elements:
        if ident_text in element.text:
            print('##### Нужный элемент найден')
            element.click()
            
def load_file_to(driver, id, path, identifier, file_name):
    # download_button = driver.find_element(By.ID, 'uploadButton')
    # download_button.click()
    driver.execute_script("UpdateFile(true)")
    
    element = WebDriverWait(driver, 10).until(
        EC.visibility_of_element_located((By.ID, "save_complete"))
    )
    # Получить URL файла
    file_url = f'{url}/document/download?id={id}'
    
    print(file_url)
    
    current_date_time = datetime.datetime.now()
    folder_name = f'{identifier} {current_date_time.strftime("%Y-%m-%d_%H-%M-%S")}'
    full_path = os.path.join(path, folder_name)
    os.makedirs(full_path, exist_ok=True)

    # Скачать файл в определенную папку
    response = requests.get(file_url, stream=True)
    with open(f'{full_path}/{file_name}', 'wb') as f:
        for chunk in response.iter_content(chunk_size=1024):
            if chunk:
                f.write(chunk)
       
       
def get_object_id(file_name):
    df = pd.read_excel(os.path.join(path_in, file_name))
    match = re.search(r'\*\*(\d+)\*\*', df.columns[0])
    return match.group(1) if match else None

def get_file_info(col_name):
    match = re.match(r'(.*)\*\*(\d+)\*\*', col_name)
    if match:
        return match.group(1), match.group(2)
    else:
        return None, None
    
def process_file(file_name):
    object_id = get_object_id(file_name)
    if object_id:
        df = pd.read_excel(os.path.join(path_in, file_name))
        for col_name in df.columns[1:]:
            file_name, id_doc = get_file_info(col_name)
            if file_name and id_doc:
                authorize_on_site(driver)
                go_to_document(driver, id_doc)
                for index, row in df.iterrows():
                    if str(row[col_name]).startswith('-'):
                        continue
                    identifier = row[df.columns[0]]
                    load_data_from_identificator(driver, f'object_{object_id}', identifier)
                    load_file_to(driver, id_doc, path_out, identifier.replace('/', '-'), f'{file_name}.docx')         
            
if __name__ == "__main__":
    logging.basicConfig(filename='log.txt', level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
    logging.basicConfig(filename='log.txt', level=logging.ERROR, format='%(asctime)s - %(levelname)s - %(message)s')
    
    with open('settings.json', 'r') as f:
        settings = json.load(f)

    url = settings['url']
    path_to_workdir = settings['path_to_workdir']
    login = settings['login']
    password = settings['password']

    path_in = f'{path_to_workdir}/in/'
    path_out = f'{path_to_workdir}/out/'
    path_processed = f'{path_to_workdir}/processed/'

    os.makedirs(path_in, exist_ok=True)
    os.makedirs(path_out, exist_ok=True)
    os.makedirs(path_processed, exist_ok=True)

    # Укажите путь к драйверу браузера
    chrome_options = Options()
    # Создайте объект драйвера
    driver = webdriver.Chrome(options=chrome_options )

    try:
        # Откройте страницу
        for file_name in os.listdir(path_in):
            if file_name.endswith('.xlsx'):
                try:
                    process_file(file_name)
                    logging.info(f'Файл {file_name} обработан успешно')
                except Exception as e:
                    logging.error(f'Ошибка при обработке файла {file_name}: {str(e)}')
                finally:
                    os.rename(os.path.join(path_in, file_name), os.path.join(path_processed, file_name))
        logging.info('Скрипт выполнен успешно')
    except Exception as e:
        logging.error(f'Ошибка при запуске скрипта: {str(e)}')

    finally:
        # Закройте браузер
        driver.quit()