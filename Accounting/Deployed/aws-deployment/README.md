# AWS Deployment Files
## Ethiopian Business Management System MVP

This directory contains all the necessary files to deploy your Ethiopian Business Management System to AWS.

## 📁 Files Overview

| File | Purpose |
|------|---------|
| `main.tf` | Terraform infrastructure configuration |
| `user_data.sh` | EC2 instance setup script |
| `deploy.sh` | Automated deployment script |
| `monitor.py` | Application monitoring script |
| `requirements-aws.txt` | AWS-specific Python dependencies |
| `DEPLOYMENT_GUIDE.md` | Detailed deployment instructions |

## 🚀 Quick Start

### 1. One-Command Deployment
```bash
chmod +x deploy.sh
./deploy.sh
```

### 2. Manual Deployment
```bash
# Initialize Terraform
terraform init

# Plan deployment 
terraform plan

# Deploy infrastructure
terraform apply
```

## 💰 Expected Costs

**Standard MVP Configuration: ~$87/month**
- EC2 t3.medium: $30.37
- RDS db.t3.small: $25.55
- Application Load Balancer: $16.43
- Storage & Monitoring: $15.65

## 📊 Monitoring

### Health Check
```bash
python3 monitor.py
```

### Continuous Monitoring
```bash
python3 monitor.py loop 300  # 5-minute intervals
```

### JSON Output (for automation)
```bash
python3 monitor.py json
```

## 🔧 Post-Deployment

After successful deployment, your system will be available at:
- **Main Application**: `http://[LOAD_BALANCER_DNS]`
- **VAT Portal**: `http://[LOAD_BALANCER_DNS]/vat/dashboard`
- **Payroll System**: `http://[LOAD_BALANCER_DNS]/payroll/dashboard`

## 🛠️ Troubleshooting

### Check Application Status
```bash
# SSH to server
ssh -i ~/.ssh/id_rsa ubuntu@[WEB_SERVER_IP]

# Check application
sudo supervisorctl status ethiopian-business

# View logs
sudo tail -f /var/log/ethiopian-business.log
```

### Common Issues
1. **Database connection failed**: Check security groups
2. **Application not starting**: Review user_data.sh logs
3. **High response times**: Monitor system resources

## 📞 Support

For deployment issues:
1. Check the detailed [DEPLOYMENT_GUIDE.md](DEPLOYMENT_GUIDE.md)
2. Review AWS CloudWatch logs
3. Run the monitoring script for diagnostics

---

**Ready to deploy?** Run `./deploy.sh` and follow the prompts! 🚀

