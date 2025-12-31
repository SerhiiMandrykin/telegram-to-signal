FROM python:3.14

RUN mkdir -p /media

RUN mkdir -p /tg-session

WORKDIR /app

COPY src/requirements.txt .

RUN pip install -r requirements.txt

COPY src/*.py .

CMD ["python", "-u", "main.py"]
