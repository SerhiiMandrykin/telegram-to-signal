FROM python:3.14

RUN apt-get update && apt-get install -y ffmpeg && rm -rf /var/lib/apt/lists/*

RUN mkdir -p /media

RUN mkdir -p /tg-session

WORKDIR /app

COPY src/requirements.txt .

RUN pip install -r requirements.txt

COPY src/ .

CMD ["python", "-u", "main.py"]
