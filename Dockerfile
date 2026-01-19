FROM python:alpine
WORKDIR /app
COPY . /app/
RUN pip install --no-cache-dir .
ENTRYPOINT ["onleiharr"]
CMD ["-c", "/app/onleiharr.toml"]
