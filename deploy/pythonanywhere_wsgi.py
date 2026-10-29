# WSGI-файл для PythonAnywhere (вкладка Web → ссылка «WSGI configuration file»).
# Замените USERNAME на своё имя пользователя и придумайте длинный SECRET_KEY.
import os
import sys

PROJECT = "/home/USERNAME/VTB_Hack"
if PROJECT not in sys.path:
    sys.path.insert(0, PROJECT)
os.chdir(PROJECT)

os.environ.setdefault("SECRET_KEY", "замените-на-длинную-случайную-строку")
# Потоки в веб-приложениях PythonAnywhere не поддерживаются, поэтому «сторож» выплат
# запускается при запросах к сайту — не чаще раза в 5 минут.
os.environ.setdefault("KEEPER_REQUEST_INTERVAL", "300")

from main import app as application  # noqa: E402
