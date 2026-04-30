# Gunicorn configuration for production
import multiprocessing

bind = "127.0.0.1:8000"
workers = multiprocessing.cpu_count() * 2 + 1
worker_class = "uvicorn.workers.UvicornWorker"
worker_connections = 1000
timeout = 120
keepalive = 5
max_requests = 1000
max_requests_jitter = 100

# Logging
accesslog = "/var/log/voter-ocr/access.log"
errorlog = "/var/log/voter-ocr/error.log"
loglevel = "info"

# Process naming
proc_name = "voter-ocr"
