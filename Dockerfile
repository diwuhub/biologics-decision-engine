FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
EXPOSE 8503
CMD ["streamlit", "run", "demo/decision_demo.py", "--server.port=8503", "--server.headless=true"]
