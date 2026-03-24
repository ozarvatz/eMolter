# Production Deployment Guide for eMolter

This guide covers deploying the eMolter Flask application to a production server.

## Prerequisites

- Ubuntu/Debian Linux server (or similar)
- Python 3.8+
- PostgreSQL or SQLite (currently using SQLite)
- Nginx web server
- Domain name with DNS configured
- SSL certificate (Let's Encrypt recommended)

## 1. Server Setup

### Install Required Packages

```bash
# Update system
sudo apt update && sudo apt upgrade -y

# Install Python and dependencies
sudo apt install -y python3.8 python3.8-venv python3-pip nginx supervisor git

# Install PostgreSQL (optional, if moving from SQLite)
sudo apt install -y postgresql postgresql-contrib
```

## 2. Application Setup

### Clone Repository and Setup Environment

```bash
# Create application directory
sudo mkdir -p /var/www/emolter
sudo chown $USER:$USER /var/www/emolter

# Clone repository
cd /var/www/emolter
git clone <your-repo-url> .
# Or copy files from development

# Create virtual environment
python3.8 -m venv venv
source venv/bin/activate

# Install dependencies
pip install --upgrade pip
pip install -r requirements.txt
pip install gunicorn  # Production WSGI server
```

## 3. Environment Configuration

### Create Production Environment File

```bash
# Create .env file
sudo nano /var/www/emolter/.env
```

Add the following (replace with your actual values):

```bash
# Flask Configuration
FLASK_ENV=production
SECRET_KEY=<generate-strong-random-key-here>

# Database
DATABASE_URL=sqlite:////var/www/emolter/app.db
# Or for PostgreSQL:
# DATABASE_URL=postgresql://username:password@localhost/emolter_db

# Twilio Configuration
TWILIO_ACCOUNT_SID=<your-twilio-sid>
TWILIO_AUTH_TOKEN=<your-twilio-token>

# Allowed phones for WhatsApp (comma-separated)
ALLOWED_PHONES=972503220778,972524519706,972546646637,4917640500988

# Application URL (your domain)
APP_URL=https://yourdomain.com
```

### Generate Secret Key

```bash
python3 -c "import secrets; print(secrets.token_hex(32))"
```

## 4. Database Setup

### Run Migrations

```bash
source venv/bin/activate
cd /var/www/emolter

# Run migrations
python manage.py db upgrade

# Create superuser
python manage.py create_superuser

# Seed prosody parameters
python manage.py seed_prosody_params

# Optional: Seed patients if needed
# python manage.py seed_patients
```

### Set Database Permissions

```bash
# Ensure database file is writable
sudo chown www-data:www-data /var/www/emolter/app.db
sudo chmod 664 /var/www/emolter/app.db
```

## 5. Gunicorn Configuration

### Create Gunicorn Configuration File

```bash
sudo nano /var/www/emolter/gunicorn_config.py
```

Add:

```python
import multiprocessing

# Server socket
bind = "127.0.0.1:8000"
backlog = 2048

# Worker processes
workers = multiprocessing.cpu_count() * 2 + 1
worker_class = "sync"
worker_connections = 1000
timeout = 120
keepalive = 5

# Logging
accesslog = "/var/log/emolter/access.log"
errorlog = "/var/log/emolter/error.log"
loglevel = "info"

# Process naming
proc_name = "emolter"

# Server mechanics
daemon = False
pidfile = "/var/run/emolter.pid"
umask = 0
user = None
group = None
tmp_upload_dir = None
```

### Create Log Directory

```bash
sudo mkdir -p /var/log/emolter
sudo chown www-data:www-data /var/log/emolter
```

## 6. Systemd Service Setup

### Create Systemd Service File

```bash
sudo nano /etc/systemd/system/emolter.service
```

Add:

```ini
[Unit]
Description=eMolter Flask Application
After=network.target

[Service]
Type=notify
User=www-data
Group=www-data
WorkingDirectory=/var/www/emolter
Environment="PATH=/var/www/emolter/venv/bin"
EnvironmentFile=/var/www/emolter/.env
ExecStart=/var/www/emolter/venv/bin/gunicorn \
    --config /var/www/emolter/gunicorn_config.py \
    --bind 127.0.0.1:8000 \
    manage:app
ExecReload=/bin/kill -s HUP $MAINPID
KillMode=mixed
TimeoutStopSec=5
PrivateTmp=true
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

### Enable and Start Service

```bash
# Set proper ownership
sudo chown -R www-data:www-data /var/www/emolter

# Reload systemd
sudo systemctl daemon-reload

# Enable service to start on boot
sudo systemctl enable emolter

# Start service
sudo systemctl start emolter

# Check status
sudo systemctl status emolter
```

## 7. Nginx Configuration

### Create Nginx Configuration

```bash
sudo nano /etc/nginx/sites-available/emolter
```

Add:

```nginx
# Rate limiting
limit_req_zone $binary_remote_addr zone=emolter_limit:10m rate=10r/s;

# Upstream configuration
upstream emolter_app {
    server 127.0.0.1:8000 fail_timeout=0;
}

# HTTP to HTTPS redirect
server {
    listen 80;
    listen [::]:80;
    server_name yourdomain.com www.yourdomain.com;

    # Let's Encrypt challenge
    location /.well-known/acme-challenge/ {
        root /var/www/certbot;
    }

    # Redirect all other traffic to HTTPS
    location / {
        return 301 https://$server_name$request_uri;
    }
}

# HTTPS server
server {
    listen 443 ssl http2;
    listen [::]:443 ssl http2;
    server_name yourdomain.com www.yourdomain.com;

    # SSL Configuration
    ssl_certificate /etc/letsencrypt/live/yourdomain.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/yourdomain.com/privkey.pem;
    ssl_trusted_certificate /etc/letsencrypt/live/yourdomain.com/chain.pem;

    # SSL Security
    ssl_protocols TLSv1.2 TLSv1.3;
    ssl_ciphers ECDHE-RSA-AES128-GCM-SHA256:ECDHE-RSA-AES256-GCM-SHA384:ECDHE-RSA-AES128-SHA256;
    ssl_prefer_server_ciphers on;
    ssl_session_cache shared:SSL:10m;
    ssl_session_timeout 10m;

    # Security Headers
    add_header Strict-Transport-Security "max-age=31536000; includeSubDomains" always;
    add_header X-Frame-Options "SAMEORIGIN" always;
    add_header X-Content-Type-Options "nosniff" always;
    add_header X-XSS-Protection "1; mode=block" always;

    # Logging
    access_log /var/log/nginx/emolter_access.log;
    error_log /var/log/nginx/emolter_error.log;

    # Client body size (for file uploads)
    client_max_body_size 10M;

    # Static files
    location /static {
        alias /var/www/emolter/automated_survey_flask/static;
        expires 30d;
        add_header Cache-Control "public, immutable";
    }

    # Application
    location / {
        # Rate limiting
        limit_req zone=emolter_limit burst=20 nodelay;

        proxy_pass http://emolter_app;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_redirect off;

        # Timeouts
        proxy_connect_timeout 120s;
        proxy_send_timeout 120s;
        proxy_read_timeout 120s;
    }

    # Twilio webhooks (no rate limiting)
    location ~ ^/(voice|handle-speech|handle-realtime-text) {
        proxy_pass http://emolter_app;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_redirect off;
    }
}
```

### Enable Site and Restart Nginx

```bash
# Enable site
sudo ln -s /etc/nginx/sites-available/emolter /etc/nginx/sites-enabled/

# Remove default site
sudo rm /etc/nginx/sites-enabled/default

# Test configuration
sudo nginx -t

# Restart Nginx
sudo systemctl restart nginx
```

## 8. SSL Certificate Setup (Let's Encrypt)

```bash
# Install Certbot
sudo apt install -y certbot python3-certbot-nginx

# Obtain certificate
sudo certbot --nginx -d yourdomain.com -d www.yourdomain.com

# Auto-renewal is enabled by default, test it:
sudo certbot renew --dry-run
```

## 9. Firewall Configuration

```bash
# Enable UFW firewall
sudo ufw enable

# Allow SSH (important!)
sudo ufw allow 22/tcp

# Allow HTTP and HTTPS
sudo ufw allow 80/tcp
sudo ufw allow 443/tcp

# Check status
sudo ufw status
```

## 10. Monitoring and Maintenance

### View Application Logs

```bash
# Application logs
sudo journalctl -u emolter -f

# Nginx logs
sudo tail -f /var/log/nginx/emolter_access.log
sudo tail -f /var/log/nginx/emolter_error.log

# Gunicorn logs
sudo tail -f /var/log/emolter/error.log
```

### Restart Application

```bash
# Restart application
sudo systemctl restart emolter

# Restart Nginx
sudo systemctl restart nginx

# Check status
sudo systemctl status emolter
sudo systemctl status nginx
```

### Update Application

```bash
cd /var/www/emolter

# Pull latest changes
git pull origin main

# Activate virtual environment
source venv/bin/activate

# Install/update dependencies
pip install -r requirements.txt

# Run migrations
python manage.py db upgrade

# Restart application
sudo systemctl restart emolter
```

## 11. Backup Strategy

### Database Backup Script

Create `/var/www/emolter/backup.sh`:

```bash
#!/bin/bash

BACKUP_DIR="/var/backups/emolter"
DATE=$(date +%Y%m%d_%H%M%S)
DB_FILE="/var/www/emolter/app.db"

mkdir -p $BACKUP_DIR

# Backup database
cp $DB_FILE "$BACKUP_DIR/app_${DATE}.db"

# Keep only last 30 days
find $BACKUP_DIR -name "app_*.db" -mtime +30 -delete

echo "Backup completed: $BACKUP_DIR/app_${DATE}.db"
```

Make executable and add to crontab:

```bash
chmod +x /var/www/emolter/backup.sh

# Add to crontab (daily at 2 AM)
sudo crontab -e
# Add line:
0 2 * * * /var/www/emolter/backup.sh
```

## 12. Security Checklist

- [ ] Strong SECRET_KEY set in .env
- [ ] Database file permissions set correctly
- [ ] Firewall enabled and configured
- [ ] SSL certificate installed and auto-renewal working
- [ ] Security headers enabled in Nginx
- [ ] Rate limiting configured
- [ ] Application running as www-data (not root)
- [ ] Environment variables not committed to git
- [ ] Backup strategy implemented
- [ ] Monitoring/logging in place

## 13. Troubleshooting

### Application Won't Start

```bash
# Check service status
sudo systemctl status emolter

# Check logs
sudo journalctl -u emolter -n 50

# Check if port is in use
sudo netstat -tlnp | grep 8000
```

### 502 Bad Gateway

```bash
# Check if Gunicorn is running
sudo systemctl status emolter

# Check Nginx error log
sudo tail -f /var/log/nginx/emolter_error.log
```

### Database Locked

```bash
# Check database file permissions
ls -l /var/www/emolter/app.db

# Fix permissions
sudo chown www-data:www-data /var/www/emolter/app.db
sudo chmod 664 /var/www/emolter/app.db
```

### Twilio Webhooks Not Working

- Ensure webhooks are configured with HTTPS URLs
- Check that Twilio webhook endpoints are excluded from rate limiting
- Verify CSRF exemptions for Twilio endpoints
- Check firewall allows incoming connections on 443

## 14. Performance Optimization

### Enable Gzip Compression in Nginx

Add to nginx configuration:

```nginx
gzip on;
gzip_vary on;
gzip_min_length 1024;
gzip_types text/plain text/css text/xml text/javascript application/json application/javascript application/xml+rss;
```

### Database Optimization (if using PostgreSQL)

```sql
-- Create indexes
CREATE INDEX idx_calls_patient_phone ON calls(patient_phone);
CREATE INDEX idx_calls_created_at ON calls(created_at);
CREATE INDEX idx_patients_therapist_id ON patients(therapist_id);
CREATE INDEX idx_users_phone ON users(phone);
```

## Support

For issues or questions:
- Check logs: `sudo journalctl -u emolter -f`
- Review Nginx logs: `/var/log/nginx/emolter_error.log`
- GitHub Issues: [Your repository URL]
