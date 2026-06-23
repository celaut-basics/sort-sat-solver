FROM python:3.11
RUN pip3 install fastapi uvicorn
COPY service /frontier
RUN chmod +x /frontier/start.sh