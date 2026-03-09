# 🚀 AWS Deployment Guide
## Ethiopian Business Management System MVP

### 📋 Pre-Deployment Checklist

**1. Generate SSH Key Pair**
```bash
ssh-keygen -t rsa -b 4096 -f ~/.ssh/id_rsa
# Press Enter for default location and empty passphrase for automation
```

**2. Configure AWS CLI**
```bash
aws configure
# AWS Access Key ID: [Your Access Key]
# AWS Secret Access Key: [Your Secret Key]  
# Default region name: af-south-1
# Default output format: json
```

**3. Verify AWS Permissions**
```bash
aws sts get-caller-identity
aws ec2 describe-regions --region af-south-1
```

**4. Prepare Your Repository**
```bash
# Upload your code to GitHub/GitLab
git remote add origin https://github.com/devopsrain/ERP.git
git push -u origin main

# Update the git clone URL in user_data.sh (line 21)
sed -i 's|https://github.com/devopsrain/ERP.git|YOUR_ACTUAL_REPO_URL|' user_data.sh
```

---

## 🏗️ Step 2: Deploy Infrastructure

**1. Initialize Terraform**
```bash
cd aws-deployment
terraform init
```

**2. Plan Deployment**
```bash
terraform plan
# Review the planned changes
# Expected resources: ~15 AWS resources
# Estimated cost: ~$90/month
```

**3. Deploy Infrastructure**
```bash
terraform apply
# Type 'yes' to confirm
# Deployment time: ~10-15 minutes
```

**4. Save Important Outputs**
```bash
# Note these values from Terraform output
terraform output load_balancer_dns
terraform output database_endpoint
terraform output web_server_ip
```

---

## 🔧 Step 3: Post-Deployment Configuration

**1. Verify EC2 Instance Setup**
```bash
# SSH into your server
ssh -i ~/.ssh/id_rsa ubuntu@$(terraform output -raw web_server_ip)

# Check application status
sudo supervisorctl status ethiopian-business
sudo systemctl status nginx
```

**2. Verify Database Connection**
```bash
# From your EC2 instance
cd /opt/ethiopian-business
source venv/bin/activate
python3 -c "
import psycopg2
import os
from dotenv import load_dotenv
load_dotenv()

try:
    conn = psycopg2.connect(os.getenv('DATABASE_URL'))
    print('✅ Database connection successful')
    conn.close()
except Exception as e:
    print(f'❌ Database connection failed: {e}')
"
```

**3. Initialize Application Data**
```bash
# Run the initialization script
cd /opt/ethiopian-business
source venv/bin/activate
python3 -c "
from core.ledger import GeneralLedger
from models.vat_portal import VATContextManager

# Setup accounting system
ledger = GeneralLedger()
ledger.create_standard_chart_of_accounts()

# Setup VAT system
vat = VATContextManager('demo_company')
print('✅ Application initialized successfully')
"
```

---

## 🌐 Step 4: Domain and SSL Setup

**1. Configure Route 53 (Optional)**
```bash
# Create hosted zone
aws route53 create-hosted-zone --name yourdomain.com --caller-reference $(date +%s)

# Create A record pointing to load balancer
aws route53 change-resource-record-sets --hosted-zone-id YOUR_ZONE_ID --change-batch '{
    "Changes": [{
        "Action": "CREATE",
        "ResourceRecordSet": {
            "Name": "yourdomain.com",
            "Type": "A",
            "AliasTarget": {
                "DNSName": "YOUR_LOAD_BALANCER_DNS",
                "EvaluateTargetHealth": false,
                "HostedZoneId": "Z1EID17UKMYQ9"
            }
        }
    }]
}'
```

**2. Request SSL Certificate**
```bash
# Request certificate from ACM
aws acm request-certificate --domain-name yourdomain.com --validation-method DNS

# Update load balancer listener for HTTPS
# (Add this to your Terraform configuration and run terraform apply)
```

---

## 📊 Step 5: Monitoring and Alerts Setup

**1. Create CloudWatch Alarms**
```bash
# High CPU Alert
aws cloudwatch put-metric-alarm \
    --alarm-name "Ethiopian-Business-HighCPU" \
    --alarm-description "High CPU utilization" \
    --metric-name CPUUtilization \
    --namespace AWS/EC2 \
    --statistic Average \
    --period 300 \
    --threshold 80 \
    --comparison-operator GreaterThanThreshold \
    --evaluation-periods 2

# Database Connection Alert
aws cloudwatch put-metric-alarm \
    --alarm-name "Ethiopian-Business-DBConnections" \
    --alarm-description "High database connections" \
    --metric-name DatabaseConnections \
    --namespace AWS/RDS \
    --statistic Average \
    --period 300 \
    --threshold 50 \
    --comparison-operator GreaterThanThreshold \
    --evaluation-periods 2
```

**2. Set up Log Monitoring**
```bash
# SSH to your server and verify logs
tail -f /var/log/ethiopian-business.log
tail -f /var/log/nginx/access.log
```

---

## 🧪 Step 6: Testing and Validation

**1. Health Check**
```bash
# Test application health endpoint
curl http://$(terraform output -raw load_balancer_dns)/health

# Expected response: {"status": "healthy", "service": "Ethiopian Business Management System"}
```

**2. Functionality Tests**
```bash
# Test login page (should be bypassed)
curl -I http://$(terraform output -raw load_balancer_dns)/

# Test VAT portal access
curl -I http://$(terraform output -raw load_balancer_dns)/vat/dashboard

# Test payroll system access  
curl -I http://$(terraform output -raw load_balancer_dns)/payroll/dashboard
```

**3. Load Testing**
```bash
# Install Apache Bench
sudo apt-get install apache2-utils

# Simple load test (100 requests, 10 concurrent)
ab -n 100 -c 10 http://$(terraform output -raw load_balancer_dns)/
```

---

## 🔐 Step 7: Security Hardening

**1. Update Security Groups**
```bash
# Restrict SSH access to your IP only
aws ec2 authorize-security-group-ingress \
    --group-id sg-xxxxxxxxx \
    --protocol tcp \
    --port 22 \
    --cidr YOUR_IP_ADDRESS/32

# Remove the 0.0.0.0/0 SSH rule
aws ec2 revoke-security-group-ingress \
    --group-id sg-xxxxxxxxx \
    --protocol tcp \
    --port 22 \
    --cidr 0.0.0.0/0
```

**2. Set up AWS Secrets Manager**
```bash
# Store database password securely
aws secretsmanager create-secret \
    --name "ethiopian-business/database" \
    --description "Database credentials" \
    --secret-string '{"username":"business_admin","password":"SecurePassword123!"}'

# Update application to use Secrets Manager
# (Modify your application configuration)
```

**3. Enable VPC Flow Logs**
```bash
aws ec2 create-flow-logs \
    --resource-type VPC \
    --resource-ids vpc-xxxxxxxxx \
    --traffic-type ALL \
    --log-destination-type cloud-watch-logs \
    --log-group-name VPCFlowLogs
```

---

## 📅 Step 8: Backup and Maintenance

**1. Verify Automated Backups**
```bash
# Check if backup script is working
sudo -u businessapp /opt/ethiopian-business/backup.sh

# Verify backup files
ls -la /opt/backups/

# Check cron job
sudo -u businessapp crontab -l
```

**2. Set up S3 Backup Sync**
```bash
# Install AWS CLI on server
ssh -i ~/.ssh/id_rsa ubuntu@$(terraform output -raw web_server_ip)

# Configure backup sync to S3
echo "0 3 * * * aws s3 sync /opt/backups/ s3://$(terraform output -raw s3_bucket_name)/backups/" | sudo -u businessapp crontab -
```

---

## 🎯 Step 9: Go-Live Checklist

**✅ Infrastructure Checklist**
- [ ] EC2 instance running and healthy
- [ ] RDS database accessible and initialized  
- [ ] Load balancer distributing traffic
- [ ] Security groups properly configured
- [ ] Backups automated and tested

**✅ Application Checklist**
- [ ] Ethiopian Business System accessible
- [ ] VAT portal functioning (income, expense, capital)
- [ ] Payroll system operational with demo data
- [ ] Authentication bypassed for MVP
- [ ] Health endpoint responding

**✅ Security Checklist**
- [ ] SSH access restricted to admin IPs
- [ ] Database in private subnets
- [ ] SSL certificate configured (optional for MVP)
- [ ] Fail2ban configured
- [ ] VPC Flow Logs enabled

**✅ Monitoring Checklist**
- [ ] CloudWatch alarms configured
- [ ] Log aggregation working
- [ ] Performance baselines established
- [ ] Backup verification scheduled

---

## 🚀 Accessing Your Application

**Primary URL**: `http://[LOAD_BALANCER_DNS]`

**System Modules**:
- **VAT Portal**: `/vat/dashboard`
- **Payroll System**: `/payroll/dashboard` 
- **Quick Transactions**: `/quick_transactions`
- **Reports**: `/chart_of_accounts`

**Health Check**: `/health`

---

## 📱 Expected Monthly Costs

| Service | Configuration | Cost |
|---------|--------------|------|
| EC2 t3.medium | 2 vCPU, 4GB RAM | $30.37 |
| RDS db.t3.small | PostgreSQL 50GB | $25.55 |
| Application Load Balancer | Standard | $16.43 |
| EBS Storage | 50GB gp3 | $4.00 |
| S3 Storage | 50GB Standard | $1.15 |
| CloudWatch | Basic monitoring | $8.00 |
| Route 53 | DNS hosting | $2.00 |
| **Total Monthly** | | **~$87.50** |

---

## 🆘 Troubleshooting

**Application Won't Start**
```bash
# Check supervisor status
sudo supervisorctl status ethiopian-business

# Check logs
sudo tail -f /var/log/ethiopian-business.log

# Restart application
sudo supervisorctl restart ethiopian-business
```

**Database Connection Issues**
```bash
# Test database connectivity
pg_isready -h [DB_ENDPOINT] -p 5432 -U business_admin

# Check security groups allow port 5432
aws ec2 describe-security-groups --group-ids [DB_SECURITY_GROUP_ID]
```

**High Response Times**
```bash
# Check system resources
htop
df -h
free -m

# Check application performance
curl -w "@curl-format.txt" -o /dev/null -s http://localhost:5000/health
```

---

Your Ethiopian Business Management System MVP is now deployed and ready for production use! 🎉