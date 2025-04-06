FROM mcr.microsoft.com/playwright/python:v1.51.0-jammy

WORKDIR /app
COPY . /app

RUN pip install --no-cache-dir -r requirements.txt

CMD ["python3", "checker.py"]
