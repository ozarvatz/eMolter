#!/bin/bash

# eMolter Production Deployment Script
# This script automates the deployment process for production

set -e  # Exit on error

echo "======================================"
echo "eMolter Production Deployment Script"
echo "======================================"
echo ""

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Configuration
APP_DIR="/var/www/emolter"
APP_USER="www-data"
VENV_DIR="$APP_DIR/venv"

# Check if running as root
if [[ $EUID -ne 0 ]]; then
   echo -e "${RED}This script must be run as root${NC}"
   exit 1
fi

# Function to print colored messages
print_info() {
    echo -e "${GREEN}[INFO]${NC} $1"
}

print_warn() {
    echo -e "${YELLOW}[WARN]${NC} $1"
}

print_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

# Check if first-time deployment or update
if [ -d "$APP_DIR" ]; then
    MODE="update"
    print_info "Existing installation detected. Running in UPDATE mode."
else
    MODE="install"
    print_info "No existing installation. Running in INSTALL mode."
fi

# 1. Update system packages
print_info "Updating system packages..."
apt update && apt upgrade -y

# 2. Install required packages (only in install mode)
if [ "$MODE" == "install" ]; then
    print_info "Installing required packages..."
    apt install -y python3.8 python3.8-venv python3-pip nginx supervisor git
fi

# 3. Create application directory
if [ "$MODE" == "install" ]; then
    print_info "Creating application directory..."
    mkdir -p $APP_DIR
    cd $APP_DIR

    # Note: You'll need to copy your application files here
    print_warn "Please copy your application files to $APP_DIR"
    print_warn "Or clone from git: git clone <repo-url> $APP_DIR"
    read -p "Press enter when files are in place..."
fi

cd $APP_DIR

# 4. Setup virtual environment
if [ ! -d "$VENV_DIR" ]; then
    print_info "Creating virtual environment..."
    python3.8 -m venv $VENV_DIR
fi

# 5. Install Python dependencies
print_info "Installing Python dependencies..."
source $VENV_DIR/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
pip install gunicorn

# 6. Check for .env file
if [ ! -f "$APP_DIR/.env" ]; then
    print_warn ".env file not found!"
    print_info "Creating template .env file..."

    SECRET_KEY=$(python3 -c "import secrets; print(secrets.token_hex(32))")

    cat > $APP_DIR/.env << EOF
# Flask Configuration
FLASK_ENV=production
SECRET_KEY=$SECRET_KEY

# Database
DATABASE_URL=sqlite:///$APP_DIR/app.db

# Twilio Configuration
TWILIO_ACCOUNT_SID=your_twilio_sid_here
TWILIO_AUTH_TOKEN=your_twilio_token_here

# Allowed phones for WhatsApp (comma-separated)
ALLOWED_PHONES=972503220778,972524519706,972546646637,4917640500988

# Application URL
APP_URL=https://yourdomain.com
EOF

    print_warn "Please edit $APP_DIR/.env with your actual values"
    read -p "Press enter when done..."
fi

# 7. Run database migrations
print_info "Running database migrations..."
python manage.py db upgrade

# 8. Seed data (only in install mode)
if [ "$MODE" == "install" ]; then
    print_info "Seeding prosody parameters..."
    python manage.py seed_prosody_params

    read -p "Do you want to create a superuser now? (y/n) " -n 1 -r
    echo
    if [[ $REPLY =~ ^[Yy]$ ]]; then
        python manage.py create_superuser
    fi
fi

# 9. Set file permissions
print_info "Setting file permissions..."
chown -R $APP_USER:$APP_USER $APP_DIR

if [ -f "$APP_DIR/app.db" ]; then
    chmod 664 $APP_DIR/app.db
fi

# 10. Create log directory
print_info "Creating log directory..."
mkdir -p /var/log/emolter
chown $APP_USER:$APP_USER /var/log/emolter

# 11. Create Gunicorn config
if [ ! -f "$APP_DIR/gunicorn_config.py" ]; then
    print_info "Creating Gunicorn configuration..."
    cat > $APP_DIR/gunicorn_config.py << 'EOF'
import multiprocessing

bind = "127.0.0.1:8000"
backlog = 2048
workers = multiprocessing.cpu_count() * 2 + 1
worker_class = "sync"
worker_connections = 1000
timeout = 120
keepalive = 5
accesslog = "/var/log/emolter/access.log"
errorlog = "/var/log/emolter/error.log"
loglevel = "info"
proc_name = "emolter"
daemon = False
pidfile = "/var/run/emolter.pid"
EOF
fi

# 12. Create systemd service
if [ ! -f "/etc/systemd/system/emolter.service" ]; then
    print_info "Creating systemd service..."
    cat > /etc/systemd/system/emolter.service << EOF
[Unit]
Description=eMolter Flask Application
After=network.target

[Service]
Type=notify
User=$APP_USER
Group=$APP_USER
WorkingDirectory=$APP_DIR
Environment="PATH=$VENV_DIR/bin"
EnvironmentFile=$APP_DIR/.env
ExecStart=$VENV_DIR/bin/gunicorn \\
    --config $APP_DIR/gunicorn_config.py \\
    --bind 127.0.0.1:8000 \\
    manage:app
ExecReload=/bin/kill -s HUP \$MAINPID
KillMode=mixed
TimeoutStopSec=5
PrivateTmp=true
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
EOF
fi

# 13. Enable and start service
print_info "Enabling and starting eMolter service..."
systemctl daemon-reload
systemctl enable emolter

if systemctl is-active --quiet emolter; then
    print_info "Restarting eMolter service..."
    systemctl restart emolter
else
    print_info "Starting eMolter service..."
    systemctl start emolter
fi

# Wait a moment for service to start
sleep 2

# 14. Check service status
if systemctl is-active --quiet emolter; then
    print_info "✅ eMolter service is running"
else
    print_error "❌ eMolter service failed to start"
    systemctl status emolter
    exit 1
fi

# 15. Setup Nginx (only prompt in install mode)
if [ "$MODE" == "install" ]; then
    echo ""
    print_info "Nginx configuration needed!"
    print_info "Please:"
    print_info "1. Copy the Nginx configuration from PRODUCTION_DEPLOYMENT.md"
    print_info "2. Place it in /etc/nginx/sites-available/emolter"
    print_info "3. Update domain names and SSL certificate paths"
    print_info "4. Enable the site: sudo ln -s /etc/nginx/sites-available/emolter /etc/nginx/sites-enabled/"
    print_info "5. Test: sudo nginx -t"
    print_info "6. Restart: sudo systemctl restart nginx"
fi

# 16. Final status check
echo ""
echo "======================================"
print_info "Deployment completed!"
echo "======================================"
echo ""
print_info "Service status:"
systemctl status emolter --no-pager -l

echo ""
print_info "Next steps:"
if [ "$MODE" == "install" ]; then
    echo "  1. Configure Nginx (see PRODUCTION_DEPLOYMENT.md)"
    echo "  2. Setup SSL certificate with Let's Encrypt"
    echo "  3. Configure firewall (ufw)"
    echo "  4. Update Twilio webhook URLs"
    echo "  5. Test the application"
else
    echo "  1. Test the application"
    echo "  2. Check logs: sudo journalctl -u emolter -f"
fi

echo ""
print_info "Useful commands:"
echo "  - View logs: sudo journalctl -u emolter -f"
echo "  - Restart: sudo systemctl restart emolter"
echo "  - Status: sudo systemctl status emolter"
echo ""
