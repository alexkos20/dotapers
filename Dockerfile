# SPBU Dashboard — образ приложения
FROM python:3.14-slim

WORKDIR /app

# Зависимости ставятся отдельным слоем — кэшируются при пересборке
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Код дашборда (при docker compose перекрывается bind-mount'ом ./dashboard)
COPY dashboard/ ./dashboard/

WORKDIR /app/dashboard
EXPOSE 8050

# debug выключен по умолчанию — один процесс, чистый останов порта
ENV DASH_DEBUG=0
CMD ["python", "app.py"]
