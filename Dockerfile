# SYS.AGENT Streamlit app image.
# The app talks to the host Docker daemon to spawn sandbox containers, so run it
# with the docker socket mounted (see docker-compose.yml).
FROM python:3.11-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
EXPOSE 8501
CMD ["streamlit", "run", "app.py", "--server.address=0.0.0.0", "--server.port=8501"]
