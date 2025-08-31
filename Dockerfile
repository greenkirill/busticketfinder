FROM python:3.12-slim

WORKDIR /app
ENV PYTHONUNBUFFERED=1

# если у тебя есть requirements.txt — ок
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# код бота
COPY . .

CMD ["python", "bot.py"]
