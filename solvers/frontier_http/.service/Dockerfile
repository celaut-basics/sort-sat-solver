# The base image is a multi-arch index, pinned by digest.
FROM python:3.11-slim@sha256:6f31d6e9ba2b0a787a3f81c37b004155b87b9efa1b771182bd550c1615745be5
COPY ./requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt && rm /tmp/requirements.txt
COPY service /frontier
RUN chmod +x /frontier/start.sh \
    && cd /frontier && python3 -B -c "import start"
