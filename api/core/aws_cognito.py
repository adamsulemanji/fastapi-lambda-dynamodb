import os
import boto3
import logging
from pydantic import EmailStr


from schemas.auth import ChangePassword, ConfirmForgotPassword, UserSignin, UserSignup, UserVerify, PhoneVerify


logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

AWS_REGION_NAME = os.getenv("AWS_REGION_NAME")
AWS_COGNITO_APP_CLIENT_ID = os.getenv("AWS_COGNITO_APP_CLIENT_ID")
AWS_COGNITO_USER_POOL_ID = os.getenv("AWS_COGNITO_USER_POOL_ID")

missing_vars = []
if not AWS_REGION_NAME: missing_vars.append("AWS_REGION_NAME")
if not AWS_COGNITO_APP_CLIENT_ID: missing_vars.append("AWS_COGNITO_APP_CLIENT_ID")
if not AWS_COGNITO_USER_POOL_ID: missing_vars.append("AWS_COGNITO_USER_POOL_ID")

if missing_vars:
    error_msg = f"AWS Cognito configuration missing: {', '.join(missing_vars)}. "
    error_msg += "For local development, please provide these in your docker run command. "
    error_msg += "For production, ensure they are set in the Lambda environment variables."
    logger.error(error_msg)
    raise ValueError(error_msg)


class AWS_Cognito:
    def __init__(self):
        try:
            logger.info(f"Initializing AWS Cognito client with region: {AWS_REGION_NAME}")
            self.client = boto3.client("cognito-idp", region_name=AWS_REGION_NAME)
            # Test the client connection
            self.client.list_user_pools(MaxResults=1)
            logger.info("Successfully initialized AWS Cognito client")
        except Exception as e:
            logger.error(f"Failed to initialize AWS Cognito client: {str(e)}")
            raise

    def user_signup(self, user: UserSignup):
        try:
            logger.info(f"Attempting user signup for email: {user.email}")
            response = self.client.sign_up(
                ClientId=AWS_COGNITO_APP_CLIENT_ID,
                Username=user.email,
                Password=user.password,
                UserAttributes=[
                    {
                        'Name': 'name',
                        'Value': user.full_name,
                    },
                    {
                        'Name': 'phone_number',
                        'Value': user.phone_number
                    },
                    {
                        'Name': 'email',
                        'Value': user.email
                    },
                ],
            )
            logger.info(f"Successfully signed up user: {user.email}")
            return response
        except Exception as e:
            logger.error(f"Failed to sign up user {user.email}: {str(e)}")
            raise

    def verify_account(self, data: UserVerify):
        response = self.client.confirm_sign_up(
            ClientId=AWS_COGNITO_APP_CLIENT_ID,
            Username=data.email,
            ConfirmationCode=data.confirmation_code,
        )

        return response

    def resend_confirmation_code(self, email: EmailStr):
        response = self.client.resend_confirmation_code(
            ClientId=AWS_COGNITO_APP_CLIENT_ID,
            Username=email
        )

        return response

    def check_user_exists(self, email: EmailStr):
        response = self.client.admin_get_user(
            UserPoolId=AWS_COGNITO_USER_POOL_ID,
            Username=email
        )

        return response

    def user_signin(self, data: UserSignin):
        response = self.client.initiate_auth(
            ClientId=AWS_COGNITO_APP_CLIENT_ID,
            AuthFlow='USER_PASSWORD_AUTH',
            AuthParameters={
                'USERNAME': data.email,
                'PASSWORD': data.password
            }
        )

        return response

    def forgot_password(self, email: EmailStr):
        response = self.client.forgot_password(
            ClientId=AWS_COGNITO_APP_CLIENT_ID,
            Username=email
        )

        return response

    def confirm_forgot_password(self, data: ConfirmForgotPassword):
        response = self.client.confirm_forgot_password(
            ClientId=AWS_COGNITO_APP_CLIENT_ID,
            Username=data.email,
            ConfirmationCode=data.confirmation_code,
            Password=data.new_password
        )

        return response

    def change_password(self, data: ChangePassword):
        response = self.client.change_password(
            PreviousPassword=data.old_password,
            ProposedPassword=data.new_password,
            AccessToken=data.access_token,
        )

        return response

    def new_access_token(self, refresh_token: str):
        response = self.client.initiate_auth(
            ClientId=AWS_COGNITO_APP_CLIENT_ID,
            AuthFlow='REFRESH_TOKEN_AUTH',
            AuthParameters={
                'REFRESH_TOKEN': refresh_token,
            }
        )

        return response

    def logout(self, access_token: str):
        response = self.client.global_sign_out(
            AccessToken = access_token
        )

        return response

    def verify_phone_number(self, user_email: EmailStr):
        """
        Request verification code for phone number
        """
        try:
            response = self.client.get_user_attribute_verification_code(
                AccessToken=self.get_admin_access_token(user_email),
                AttributeName='phone_number'
            )
            logger.info(f"Phone verification code sent for user: {user_email}")
            return response
        except Exception as e:
            logger.error(f"Failed to send phone verification code for {user_email}: {str(e)}")
            raise

    def confirm_phone_verification(self, data: PhoneVerify):
        """
        Verify phone number with the confirmation code
        """
        try:
            response = self.client.verify_user_attribute(
                AccessToken=self.get_admin_access_token(data.email),
                AttributeName='phone_number',
                Code=data.verification_code
            )
            logger.info(f"Phone number verified for user: {data.email}")
            return response
        except Exception as e:
            logger.error(f"Failed to verify phone number for {data.email}: {str(e)}")
            raise
            
    def get_admin_access_token(self, email: EmailStr):
        """
        Get an access token for the user (for admin operations)
        This is a helper function for phone verification
        In production, you would use the user's own token
        """
        try:
            # In a real implementation, this should use the user's actual access token
            # This is a simplified version for demonstration
            response = self.client.admin_initiate_auth(
                UserPoolId=AWS_COGNITO_USER_POOL_ID,
                ClientId=AWS_COGNITO_APP_CLIENT_ID,
                AuthFlow='ADMIN_NO_SRP_AUTH',
                AuthParameters={
                    'USERNAME': email,
                    'PASSWORD': 'temporary_password'  # This is for demo only
                }
            )
            return response['AuthenticationResult']['AccessToken']
        except Exception as e:
            logger.error(f"Failed to get admin access token for {email}: {str(e)}")
            raise
            
    def get_user_info_from_token(self, access_token: str):
        """
        Get user information from an access token
        """
        try:
            response = self.client.get_user(
                AccessToken=access_token
            )
            return response
        except Exception as e:
            logger.error(f"Failed to get user info from token: {str(e)}")
            raise