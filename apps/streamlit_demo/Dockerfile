FROM python:3.12-slim

WORKDIR /app

COPY apps/streamlit_demo/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY apps/streamlit_demo /app/apps/streamlit_demo
COPY data/deploy /app/data/deploy

ENV PYTHONPATH=/app/apps/streamlit_demo
ENV GRID_RUSH_ROOT=/app
ENV GRID_RUSH_USE_DEPLOY=1
ENV STREAMLIT_SERVER_PORT=8501
ENV STREAMLIT_SERVER_ADDRESS=0.0.0.0
ENV STREAMLIT_BROWSER_GATHER_USAGE_STATS=false

EXPOSE 8501

CMD ["streamlit", "run", "apps/streamlit_demo/app.py", "--server.port=8501", "--server.address=0.0.0.0"]
