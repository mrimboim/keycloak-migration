import os
import json
import argparse
import logging
from datetime import datetime
from typing import Dict, List
import requests
from dotenv import load_dotenv
from descope import DescopeClient
import time

# Load environment variables from .env file
load_dotenv()

# Configure logging
log_dir = "logs"
os.makedirs(log_dir, exist_ok=True)
timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
log_filename = os.path.join(log_dir, f"user_migration_{timestamp}.log")

logging.basicConfig(
    filename=log_filename,
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)

class KeycloakMigrationTool:
    def __init__(self, path: str, realm: str):
        self.path = path
        self.realm = realm
        self.project_id = os.getenv('DESCOPE_PROJECT_ID')
        self.management_key = os.getenv('DESCOPE_MANAGEMENT_KEY')
       
        if not self.project_id or not self.management_key:
            raise ValueError("Environment variables DESCOPE_PROJECT_ID and DESCOPE_MANAGEMENT_KEY must be set.")

        self.descope_client = DescopeClient(project_id=self.project_id, management_key=self.management_key)
    
    def create_roles_in_descope(self) -> None:
        """Create roles in Descope that exist in Keycloak but not in Descope"""
        print("Creating roles in Descope...")
        # Consolidate role fetching into a helper method
        keycloak_roles = self.get_keycloak_roles()
        descope_roles = self.get_descope_roles()
        
        # Create roles that exist in Keycloak but not in Descope
        unique_roles = set(keycloak_roles) - set(descope_roles)
        num_roles = 0
        
        for role_name in unique_roles:
            try:
                self.descope_client.mgmt.role.create(name=role_name)
                logging.info(f"Created role in Descope: {role_name}")
                num_roles += 1
            except Exception as e:
                logging.error(f"Failed to create role {role_name}: {str(e)}")
                
        print(f"Created {num_roles} roles in Descope")

    def get_descope_roles(self) -> List[str]:
        """Get existing roles from Descope"""
        try:
            roles_resp = self.descope_client.mgmt.role.load_all()
            return [role['name'] for role in roles_resp["roles"]]
        except Exception as e:
            logging.error(f"Failed to get Descope roles: {str(e)}")
            return []

    def get_keycloak_roles(self) -> List[str]:
        """Get roles from Keycloak realm files"""
        keycloak_roles = []
        try:
            file_pattern = f"{self.realm}-realm"
            for file_name in os.listdir(self.path):
                if file_name.startswith(file_pattern) and file_name.endswith('.json'):
                    with open(os.path.join(self.path, file_name), 'r') as f:
                        file_data = json.load(f)
                        # Get realm roles
                        keycloak_roles.extend(role["name"] for role in file_data.get("roles", {}).get("realm", []))
                        # Get client roles
                        for client_roles in file_data.get("roles", {}).get("client", {}).values():
                            keycloak_roles.extend(role["name"] for role in client_roles)
            return keycloak_roles
        except Exception as e:
            logging.error(f"Failed to get Keycloak roles: {str(e)}")
            return []

    def create_groups_in_descope(self) -> None:
        """Create groups in Descope that exist in Keycloak but not in Descope"""
        print("Creating groups in Descope...")
        try:
            keycloak_groups = self.get_keycloak_groups()
            descope_groups = self.get_descope_groups()
            
            # Create groups that exist in Keycloak but not in Descope
            unique_groups = set(keycloak_groups) - set(descope_groups)
            num_groups = 0
            
            for group_name in unique_groups:
                try:
                    self.descope_client.mgmt.tenant.create(name=group_name, id=group_name)
                    logging.info(f"Created group in Descope: {group_name}")
                    num_groups += 1
                except Exception as e:
                    logging.error(f"Failed to create group {group_name}: {str(e)}")
                
            print(f"Created {num_groups} groups in Descope")
        except Exception as e:
            logging.error(f"Failed to create groups: {str(e)}")

    def get_descope_groups(self) -> List[str]:
        """Get existing tenants from Descope"""
        try:
            tenants_resp = self.descope_client.mgmt.tenant.load_all()
            return [tenant['id'] for tenant in tenants_resp["tenants"]]
        except Exception as e:
            logging.error(f"Failed to get Descope tenants: {str(e)}")
            return []

    def get_keycloak_groups(self) -> List[str]:
        """Get groups from Keycloak realm files"""
        try:
            file_pattern = f"{self.realm}-realm"
            for file_name in os.listdir(self.path):
                if file_name.startswith(file_pattern) and file_name.endswith('.json'):
                    with open(os.path.join(self.path, file_name), 'r') as f:
                        file_data = json.load(f)
                        return [group["name"] for group in file_data.get("groups", [])]
            return []
        except Exception as e:
            logging.error(f"Failed to get Keycloak groups: {str(e)}")
            return []

    def process_users(self) -> None:
        """Process all user export files in the specified directory that match the realm"""
        try:
            file_pattern = f"{self.realm}-users-"
            user_count = 0
            last_print = 0  # Track the last printed tens value
            print("Starting user migration...")
            for file_name in os.listdir(self.path):
                if file_name.startswith(file_pattern) and file_name.endswith('.json'):
                    file_path = os.path.join(self.path, file_name)
                    with open(file_path, 'r') as f:
                        file_data = json.load(f)
                    
                    if isinstance(file_data, dict) and "users" in file_data:
                        users_data = file_data["users"]
                        num_users = self.batch_create_users(users_data)
                        user_count += num_users
                        time.sleep(1)
                        # Only print when we reach a new tens value
                        current_tens = user_count // 10
                        if current_tens > last_print:
                            print(f"Processed {user_count} users...")
                            last_print = current_tens
                    else:
                        logging.error(f"Invalid file format in {file_path}: missing 'users' array")
            
            print(f"Migration complete. Total users processed: {user_count}")
        except Exception as e:
            logging.error(f"Failed to process files in {self.path}: {str(e)}")

    def batch_create_users(self, users_data: List[Dict]) -> int:
        """Batch create users in Descope"""
        user_batch = []
        disabled_users = []
        try: 
            for user_data in users_data:
                email = user_data.get("email")
                username = user_data.get("username")
                verified_email = user_data.get("emailVerified", False)
                
                # Determine loginId and additionalIdentifiers
                login_id = username if username else email

                user_roles = user_data.get("realmRoles", [])
                for clientRoles in user_data.get("clientRoles",{}).values():
                    user_roles.extend(clientRoles)

                user_tenants = [ {"tenantId": group.lstrip("/")} for group in user_data.get("groups", [])]
                
                additional_identifiers = [email] if username else []
                if user_data.get("enabled") == False:
                    disabled_users.append(login_id)
                # Prepare hashedPassword
                credentials = user_data.get("credentials", [])
                hashed_password = self.process_credentials(credentials)

                # Prepare payload
                user = {
                    "loginId": login_id,
                    "email": email,
                    "verifiedEmail": verified_email,
                    "additionalIdentifiers": additional_identifiers,
                    "hashedPassword": hashed_password,
                    "roleNames": user_roles,
                    "userTenants": user_tenants
                }

                user_batch.append(user)

            # Prepare payload
            payload = {
                "users": user_batch,
                "invite": False,
                "sendMail": False,
                "sendSMS": False,
            }

            # API request
            url = "https://api.descope.com/v1/mgmt/user/create/batch"
            headers = {
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.project_id}:{self.management_key}"
            }

            response = requests.post(url, headers=headers, json=payload)

            for disabled_user in disabled_users:
                self.descope_client.mgmt.user.deactivate(login_id=disabled_user)

            num_users = len(user_batch)

            if response.status_code == 200:
                logging.info(f"Successfully created {num_users} users")
            else:
                logging.error(f"Failed to create {num_users} users: {response.status_code} - {response.text}")

            return num_users

        except Exception as e:
            logging.error(f"Failed to create {num_users} users: {str(e)}")
    
    def process_credentials(self, credentials: List[Dict]) -> Dict:
        """Process Keycloak credentials into Descope format"""

        for credential in credentials:
            if credential.get("type") == "password":
                secret_data = json.loads(credential.get("secretData", "{}"))
                cred_data = json.loads(credential.get("credentialData", "{}"))
                return {
                    "argon2": {
                        "hash": secret_data.get("value", ""),
                        "salt": secret_data.get("salt", ""),
                        "iterations": cred_data.get("hashIterations", 3),
                        "memory": int(cred_data.get("additionalParameters", {}).get("memory", ["7168"])[0]),
                        "threads": int(cred_data.get("additionalParameters", {}).get("parallelism", ["1"])[0])
                    }
                }
        return None

def main():
    parser = argparse.ArgumentParser(description='Create users in Descope from Keycloak export files')
    parser.add_argument('--path', required=True, help='Path to the exported users folder')
    parser.add_argument('--realm', required=True, help='Name of the Keycloak realm')
    args = parser.parse_args()

    migration_tool = KeycloakMigrationTool(args.path, args.realm)
    migration_tool.create_roles_in_descope()
    migration_tool.create_groups_in_descope()
    migration_tool.process_users()

if __name__ == "__main__":
    main() 