import os
import json
import argparse
import logging
from datetime import datetime
from typing import Dict, List
import requests
from dotenv import load_dotenv
from descope import DescopeClient
from descope.management.user import UserObj
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
        #Get all existing roles and groups from descope
        descope_roles = []
        keycloak_roles = []
        num_roles = 0
        try:
            roles_resp = self.descope_client.mgmt.role.load_all()
            roles = roles_resp["roles"]
            descope_roles = [role['name'] for role in roles]
        except Exception as e:
            logging.error(f"Failed to get roles: {str(e)}")

        try:
            file_pattern = f"{self.realm}-realm"
            for file_name in os.listdir(self.path):
                if file_name.startswith(file_pattern) and file_name.endswith('.json'):
                    file_path = os.path.join(self.path, file_name)
                    with open(file_path, 'r') as f:
                        file_data = json.load(f)
                        for role in file_data.get("roles", {}).get("realm", []):
                            keycloak_roles.append(role["name"])
                        for client_roles in file_data.get("roles",{}).get("client",{}).values():
                            for role in client_roles:
                                keycloak_roles.append(role["name"])

        except Exception as e:
            logging.error(f"Failed to get keycloak roles from {file_path}: {str(e)}") 
                        
        try:
            # Create roles that exist in Keycloak but not in Descope
            unique_roles = set(keycloak_roles) - set(descope_roles)
            for role_name in unique_roles:
                try:
                    self.descope_client.mgmt.role.create(name=role_name)
                    logging.info(f"Created role in Descope: {role_name}")
                    num_roles += 1
                except Exception as e:
                    logging.error(f"Failed to create role {role_name}: {str(e)}")
        except Exception as e:
            logging.error(f"Failed to create roles: {str(e)}")  
        print(f"Created {num_roles} roles in Descope")



    def create_groups_in_descope(self) -> None:
        descope_groups = []
        keycloak_groups = []
        num_groups = 0
        try:
            tenants_resp = self.descope_client.mgmt.tenant.load_all()
            tenants = tenants_resp["tenants"]
            descope_groups = [tenant['id'] for tenant in tenants]
        except Exception as e:
            logging.error(f"Failed to get tenants: {str(e)}")

        try: 
            file_pattern = f"{self.realm}-realm"
            for file_name in os.listdir(self.path):
                if file_name.startswith(file_pattern) and file_name.endswith('.json'):
                    file_path = os.path.join(self.path, file_name)
                    with open(file_path, 'r') as f:
                        file_data = json.load(f)
                        keycloak_groups = [group["name"] for group in file_data.get("groups", [])]
        except Exception as e:
            logging.error(f"Failed to get keycloak groups from {file_path}: {str(e)}")

        try:
            # Create groups that exist in Keycloak but not in Descope
            unique_groups = set(keycloak_groups) - set(descope_groups)
            for group_name in unique_groups:
                try:
                    self.descope_client.mgmt.tenant.create(name=group_name,id=group_name)
                    logging.info(f"Created group in Descope: {group_name}")
                    num_groups += 1
                except Exception as e:
                    logging.error(f"Failed to create group {group_name}: {str(e)}")
        except Exception as e:
            logging.error(f"Failed to create groups: {str(e)}")
        print(f"Created {num_groups} groups in Descope")


    def process_files(self) -> None:
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
                hashed_password = None
                for credential in credentials:
                    if credential.get("type") == "password":
                        secret_data = json.loads(credential.get("secretData", "{}"))
                        cred_data = json.loads(credential.get("credentialData", "{}"))
                        hashed_password = {
                            "argon2": {
                                "hash": secret_data.get("value", ""),
                                "salt": secret_data.get("salt", ""),
                                "iterations": cred_data.get("hashIterations", 3),
                                "memory": int(cred_data.get("additionalParameters", {}).get("memory", ["7168"])[0]),
                                "threads": int(cred_data.get("additionalParameters", {}).get("parallelism", ["1"])[0])
                            }
                        }
                        break

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
    
def main():
    parser = argparse.ArgumentParser(description='Create users in Descope from Keycloak export files')
    parser.add_argument('--path', required=True, help='Path to the exported users folder')
    parser.add_argument('--realm', required=True, help='Name of the Keycloak realm')
    args = parser.parse_args()

    migration_tool = KeycloakMigrationTool(args.path, args.realm)
    migration_tool.create_roles_in_descope()
    migration_tool.create_groups_in_descope()
    migration_tool.process_files()

if __name__ == "__main__":
    main() 