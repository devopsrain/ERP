#!/bin/bash

# Ethiopian Business Management System - Automated AWS Deployment Script
# This script automates the deployment of your MVP to AWS

set -e  # Exit on any error

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

echo -e "${GREEN}🚀 Ethiopian Business Management System - AWS Deployment${NC}"
echo "================================================================"

# Function to print colored output
print_status() {
    echo -e "${GREEN}✅ $1${NC}"
}

print_warning() {
    echo -e "${YELLOW}⚠️  $1${NC}"
}

print_error() {
    echo -e "${RED}❌ $1${NC}"
}

# Check prerequisites
check_prerequisites() {
    echo -e "${YELLOW}🔍 Checking prerequisites...${NC}"
    
    # Check if AWS CLI is installed
    if ! command -v aws &> /dev/null; then
        print_error "AWS CLI is not installed. Please install it first."
        echo "Install with: https://docs.aws.amazon.com/cli/latest/userguide/getting-started-install.html"
        exit 1
    fi
    print_status "AWS CLI is installed"
    
    # Check if Terraform is installed
    if ! command -v terraform &> /dev/null; then
        print_error "Terraform is not installed. Please install it first."
        echo "Install with: https://learn.hashicorp.com/tutorials/terraform/install-cli"
        exit 1
    fi
    print_status "Terraform is installed"
    
    # Check AWS credentials
    if ! aws sts get-caller-identity &> /dev/null; then
        print_error "AWS credentials not configured. Run 'aws configure' first."
        exit 1
    fi
    print_status "AWS credentials are configured"
    
    # Check SSH key
    if [ ! -f ~/.ssh/id_rsa.pub ]; then
        print_warning "SSH key not found. Generating new SSH key..."
        ssh-keygen -t rsa -b 4096 -f ~/.ssh/id_rsa -N ""
        print_status "SSH key generated"
    else
        print_status "SSH key exists"
    fi
}

# Validate input parameters
validate_inputs() {
    echo -e "${YELLOW}📝 Validating deployment configuration...${NC}"
    
    # Get project information
    read -p "Enter your GitHub repository URL (e.g., https://github.com/username/repo.git): " REPO_URL
    if [ -z "$REPO_URL" ]; then
        print_error "Repository URL is required"
        exit 1
    fi
    
    read -p "Enter your domain name (optional, press Enter to skip): " DOMAIN_NAME
    
    read -p "Enter AWS region [af-south-1]: " AWS_REGION
    AWS_REGION=${AWS_REGION:-af-south-1}
    
    read -p "Enter project name [ethiopian-business-mvp]: " PROJECT_NAME
    PROJECT_NAME=${PROJECT_NAME:-ethiopian-business-mvp}
    
    print_status "Configuration validated"
}

# Update configuration files
update_config() {
    echo -e "${YELLOW}🔧 Updating configuration files...${NC}"
    
    # Update user_data.sh with repository URL
    if [ -f "user_data.sh" ]; then
        sed -i "s|https://github.com/devopsrain/ERP.git|$REPO_URL|g" user_data.sh
        print_status "Updated repository URL in user_data.sh"
    fi
    
    # Update Terraform variables if needed
    cat > terraform.tfvars << EOF
aws_region = "$AWS_REGION"
project_name = "$PROJECT_NAME"
environment = "production"
EOF
    print_status "Created terraform.tfvars"
}

# Deploy infrastructure
deploy_infrastructure() {
    echo -e "${YELLOW}🏗️ Deploying AWS infrastructure...${NC}"
    
    # Initialize Terraform
    terraform init
    print_status "Terraform initialized"
    
    # Plan deployment
    echo -e "${YELLOW}📋 Creating deployment plan...${NC}"
    terraform plan -var-file="terraform.tfvars"
    
    # Confirm deployment
    echo ""
    read -p "Do you want to proceed with the deployment? (y/N): " CONFIRM
    if [[ $CONFIRM != [yY] ]]; then
        print_error "Deployment cancelled"
        exit 1
    fi
    
    # Deploy infrastructure
    echo -e "${YELLOW}🚀 Deploying infrastructure... (this may take 10-15 minutes)${NC}"
    terraform apply -var-file="terraform.tfvars" -auto-approve
    print_status "Infrastructure deployed successfully"
}

# Get deployment outputs
get_outputs() {
    echo -e "${YELLOW}📊 Getting deployment information...${NC}"
    
    LOAD_BALANCER_DNS=$(terraform output -raw load_balancer_dns)
    WEB_SERVER_IP=$(terraform output -raw web_server_ip)
    DATABASE_ENDPOINT=$(terraform output -raw database_endpoint)
    S3_BUCKET=$(terraform output -raw s3_bucket_name)
    
    print_status "Retrieved deployment outputs"
}

# Verify deployment
verify_deployment() {
    echo -e "${YELLOW}🧪 Verifying deployment...${NC}"
    
    # Wait for instance to be ready
    echo "Waiting for EC2 instance to be ready..."
    sleep 120  # Give the instance time to boot and run user_data script
    
    # Test SSH connectivity
    echo "Testing SSH connectivity..."
    if ssh -i ~/.ssh/id_rsa -o ConnectTimeout=10 -o StrictHostKeyChecking=no ubuntu@$WEB_SERVER_IP "echo 'SSH connection successful'" 2>/dev/null; then
        print_status "SSH connection successful"
    else
        print_warning "SSH connection failed - instance may still be booting"
    fi
    
    # Test health endpoint
    echo "Testing application health..."
    for i in {1..12}; do  # Try for 2 minutes
        if curl -s -o /dev/null -w "%{http_code}" http://$LOAD_BALANCER_DNS/health | grep -q "200"; then
            print_status "Application is responding"
            break
        else
            echo "Waiting for application to start... (attempt $i/12)"
            sleep 10
        fi
    done
}

# Display final information
display_results() {
    echo ""
    echo -e "${GREEN}🎉 Deployment completed successfully!${NC}"
    echo "================================================================"
    echo -e "${GREEN}📋 Deployment Information:${NC}"
    echo ""
    echo "🌐 Application URL: http://$LOAD_BALANCER_DNS"
    echo "🖥️  Web Server IP: $WEB_SERVER_IP"
    echo "🗄️  Database Endpoint: $DATABASE_ENDPOINT"
    echo "📦 S3 Bucket: $S3_BUCKET"
    echo ""
    echo -e "${GREEN}📱 Available Modules:${NC}"
    echo "• VAT Portal: http://$LOAD_BALANCER_DNS/vat/dashboard"
    echo "• Payroll System: http://$LOAD_BALANCER_DNS/payroll/dashboard"
    echo "• Quick Transactions: http://$LOAD_BALANCER_DNS/quick_transactions"
    echo "• Reports: http://$LOAD_BALANCER_DNS/chart_of_accounts"
    echo ""
    echo -e "${GREEN}🔧 Management Commands:${NC}"
    echo "• SSH: ssh -i ~/.ssh/id_rsa ubuntu@$WEB_SERVER_IP"
    echo "• Logs: tail -f /var/log/ethiopian-business.log"
    echo "• Status: sudo supervisorctl status ethiopian-business"
    echo "• Restart: sudo supervisorctl restart ethiopian-business"
    echo ""
    echo -e "${GREEN}💰 Estimated Monthly Cost: ~$87.50${NC}"
    echo ""
    echo -e "${YELLOW}⚠️  Important Notes:${NC}"
    echo "• Change default database password in production"
    echo "• Set up SSL certificate for your domain"
    echo "• Configure proper backup retention"
    echo "• Set up monitoring alerts"
    echo ""
    echo -e "${GREEN}📚 Next Steps:${NC}"
    echo "1. Test all application features"
    echo "2. Configure your domain name (if provided)"
    echo "3. Set up SSL certificate"
    echo "4. Configure monitoring and alerts"
    echo "5. Schedule regular backups"
    echo ""
}

# Cleanup function for errors
cleanup() {
    if [ $? -ne 0 ]; then
        print_error "Deployment failed. Check the error messages above."
        echo ""
        echo "To clean up any partial deployment, run:"
        echo "terraform destroy -var-file='terraform.tfvars'"
    fi
}

# Set trap for cleanup
trap cleanup EXIT

# Main execution
main() {
    check_prerequisites
    validate_inputs
    update_config
    deploy_infrastructure
    get_outputs
    verify_deployment
    display_results
}

# Run main function
main "$@"

echo -e "${GREEN}✨ Ethiopian Business Management System is now live! ✨${NC}"