FROM python:3.11-slim

WORKDIR /app

RUN pip install --no-cache-dir --upgrade pip

COPY . .
RUN pip install --no-cache-dir .

EXPOSE 7474

ENTRYPOINT ["firecloud"]
