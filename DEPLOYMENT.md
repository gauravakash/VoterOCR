# Deployment Guide — Hostinger VPS

## Prerequisites

- Ubuntu/Debian VPS with root/sudo access
- Python 3.11+
- Minimum 2GB RAM, 20GB disk
- Domain name (optional but recommended)

## Step 1: Server Setup

```bash
# Update system
sudo apt update && sudo apt upgrade -y

# Install dependencies
sudo apt install -y python3 python3-venv python3-dev build-essential \
    nginx git curl wget supervisor postgresql postgresql-contrib

# Create application user
sudo useradd -r -s /bin/bash voter-ocr
sudo mkdir -p /var/www/voter-ocr
sudo chown voter-ocr:voter-ocr /var/www/voter-ocr
```

## Step 2: Clone & Setup Application

```bash
cd /var/www/voter-ocr
sudo -u voter-ocr git clone https://github.com/gauravakash/VoterOCR.git .

# Create virtual environment
sudo -u voter-ocr python3 -m venv venv

# Install dependencies
sudo -u voter-ocr ./venv/bin/pip install --upgrade pip
sudo -u voter-ocr ./venv/bin/pip install -r requirements.txt
sudo -u voter-ocr ./venv/bin/pip install gunicorn uvicorn[standard]

# Create log directory
sudo mkdir -p /var/log/voter-ocr
sudo chown voter-ocr:voter-ocr /var/log/voter-ocr
sudo chmod 755 /var/log/voter-ocr

# Create data directories
sudo -u voter-ocr mkdir -p /var/www/voter-ocr/data/uploads
sudo -u voter-ocr mkdir -p /var/www/voter-ocr/data/outputs
```

## Step 3: Environment Configuration

```bash
# Copy and edit .env
sudo -u voter-ocr cp .env.example .env

# Edit with your actual API keys
sudo nano /var/www/voter-ocr/.env
```

Required .env settings for production:

```env
LLM_PROVIDER=gemini
GOOGLE_API_KEY=your_actual_key_here
VISION_MODEL=gemini-2.5-pro
TEXT_MODEL=gemini-2.5-flash

# Paths (should match service file)
UPLOAD_DIR=/var/www/voter-ocr/data/uploads
OUTPUT_DIR=/var/www/voter-ocr/data/outputs
DB_PATH=/var/www/voter-ocr/data/jobs.db

# Performance tuning for VPS
MAX_CONCURRENT_PDFS=2
MAX_CONCURRENT_PAGES_PER_PDF=3
MAX_UPLOAD_SIZE_MB=100

# Server
PORT=8000
```

## Step 4: Systemd Service Setup

```bash
# Copy service file
sudo cp /var/www/voter-ocr/voter-ocr.service /etc/systemd/system/

# Enable and start service
sudo systemctl daemon-reload
sudo systemctl enable voter-ocr
sudo systemctl start voter-ocr

# Check status
sudo systemctl status voter-ocr
```

## Step 5: Nginx Configuration

```bash
# Copy nginx config
sudo cp /var/www/voter-ocr/nginx.conf /etc/nginx/sites-available/voter-ocr

# Edit to set your domain
sudo nano /etc/nginx/sites-available/voter-ocr

# Enable site
sudo ln -s /etc/nginx/sites-available/voter-ocr /etc/nginx/sites-enabled/

# Test configuration
sudo nginx -t

# Reload nginx
sudo systemctl reload nginx
```

## Step 6: SSL Certificate (Let's Encrypt)

```bash
# Install certbot
sudo apt install -y certbot python3-certbot-nginx

# Get certificate (replace with your domain)
sudo certbot certonly --nginx -d your-domain.com

# Auto-renewal should be configured by default
sudo systemctl enable certbot.timer
```

## Step 7: Firewall

```bash
# Enable UFW (if not already enabled)
sudo ufw enable

# Allow SSH, HTTP, HTTPS
sudo ufw allow 22/tcp
sudo ufw allow 80/tcp
sudo ufw allow 443/tcp
```

## Monitoring & Maintenance

### View Logs

```bash
# Application logs
sudo tail -f /var/log/voter-ocr/error.log
sudo tail -f /var/log/voter-ocr/access.log

# Systemd logs
sudo journalctl -u voter-ocr -f

# Nginx logs
sudo tail -f /var/log/nginx/error.log
```

### Restart Service

```bash
sudo systemctl restart voter-ocr
```

### Check Memory Usage

```bash
ps aux | grep gunicorn
free -h
```

### Database Backup

```bash
# Backup job database
sudo -u voter-ocr cp /var/www/voter-ocr/data/jobs.db \
    /var/www/voter-ocr/data/jobs.db.backup.$(date +%Y%m%d)
```

## Troubleshooting

### Service won't start
```bash
sudo systemctl status voter-ocr
sudo journalctl -u voter-ocr -n 50
```

### Port already in use
```bash
sudo lsof -i :8000
```

### Permission denied
```bash
sudo chown -R voter-ocr:voter-ocr /var/www/voter-ocr
sudo chmod -R 755 /var/www/voter-ocr
```

### API Key issues
```bash
# Test if .env is readable by service user
sudo -u voter-ocr cat /var/www/voter-ocr/.env | grep GOOGLE
```

## Production Checklist

- [ ] Python 3.11+ installed
- [ ] Virtual environment created and dependencies installed
- [ ] .env configured with API keys
- [ ] Data directories created with proper permissions
- [ ] Systemd service installed and running
- [ ] Nginx reverse proxy configured
- [ ] SSL certificate installed
- [ ] Firewall rules configured
- [ ] Log rotation configured (optional)
- [ ] Backup strategy implemented
- [ ] Domain pointing to server IP

## Scaling Tips

For larger deployments:

1. **Multiple workers**: Adjust `workers` in gunicorn_config.py
2. **Database**: Switch from SQLite to PostgreSQL
3. **Caching**: Add Redis for job queue
4. **Monitoring**: Set up Prometheus + Grafana
5. **Load Balancing**: Use multiple instances behind HAProxy

---

For support, check logs in `/var/log/voter-ocr/` and application README.md
