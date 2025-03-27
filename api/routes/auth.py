from fastapi import APIRouter, status, Depends, HTTPException
from pydantic import EmailStr


from schemas.auth import AccessToken, ChangePassword, ConfirmForgotPassword, PhoneVerify, RefreshToken, UserSignin, UserSignup, UserVerify
from services.auth import AuthService
from core.aws_cognito import AWS_Cognito
from core.dependencies import get_aws_cognito, get_current_user


router = APIRouter()

@router.get("/")
def base():
    return "Hi, this is my base route from the auth router"


# USER SIGNUP
@router.post('/signup', status_code=status.HTTP_201_CREATED, tags=['Auth'])
async def signup_user(user: UserSignup, cognito: AWS_Cognito = Depends(get_aws_cognito)):
    return AuthService.user_signup(user, cognito)


@router.post('/verify_account', status_code=status.HTTP_200_OK, tags=["Auth"])
async def verify_account(
    data: UserVerify,
    cognito: AWS_Cognito = Depends(get_aws_cognito),
):
    return AuthService.verify_account(data, cognito)


# RESEND CONFIRMATION CODE
@router.post('/resend_confirmation_code', status_code=status.HTTP_200_OK, tags=['Auth'])
async def resend_confirmation_code(email: EmailStr, cognito: AWS_Cognito = Depends(get_aws_cognito)):
    return AuthService.resend_confirmation_code(email, cognito)


# USER SIGNIN
@router.post('/signin', status_code=status.HTTP_200_OK, tags=["Auth"])
async def signin(data: UserSignin, cognito: AWS_Cognito = Depends(get_aws_cognito)):
    return AuthService.user_signin(data, cognito)


# FORGOT PASSWORD
@router.post('/forgot_password', status_code=status.HTTP_200_OK, tags=["Auth"])
async def forgot_password(email: EmailStr, cognito: AWS_Cognito = Depends(get_aws_cognito)):
    return AuthService.forgot_password(email, cognito)


# CONFIRM FORGOT PASSWORD
@router.post('/confirm_forgot_password', status_code=status.HTTP_200_OK, tags=["Auth"])
async def confirm_forgot_password(data: ConfirmForgotPassword, cognito: AWS_Cognito = Depends(get_aws_cognito)):
    return AuthService.confirm_forgot_password(data, cognito)


# CHANGE PASSWORD
@router.post('/change_password', status_code=status.HTTP_200_OK, tags=["Auth"])
async def change_password(data: ChangePassword, cognito: AWS_Cognito = Depends(get_aws_cognito)):
    return AuthService.change_password(data, cognito)


# GENERATE NEW ACCESS TOKEN
@router.post('/new_token', status_code=status.HTTP_200_OK, tags=["Auth"])
async def new_access_token(refresh_token: RefreshToken, cognito: AWS_Cognito = Depends(get_aws_cognito)):
    return AuthService.new_access_token(refresh_token.refresh_token, cognito)


# LOGOUT
@router.post('/logout', status_code=status.HTTP_204_NO_CONTENT, tags=["Auth"])
async def logout(access_token: AccessToken, cognito: AWS_Cognito = Depends(get_aws_cognito)):
    return AuthService.logout(access_token.access_token, cognito)


# GET USER DETAILS
@router.get('/user_details', status_code=status.HTTP_200_OK, tags=["Auth"])
async def user_details(email: EmailStr, cognito: AWS_Cognito = Depends(get_aws_cognito)):
    return AuthService.user_details(email, cognito)


# PHONE VERIFICATION
@router.post('/request_phone_verification', status_code=status.HTTP_200_OK, tags=["Auth"])
async def request_phone_verification(email: EmailStr, cognito: AWS_Cognito = Depends(get_aws_cognito)):
    return AuthService.request_phone_verification(email, cognito)

@router.post('/confirm_phone_verification', status_code=status.HTTP_200_OK, tags=["Auth"])
async def confirm_phone_verification(data: PhoneVerify, cognito: AWS_Cognito = Depends(get_aws_cognito)):
    return AuthService.confirm_phone_verification(data, cognito)


# PROTECTED ROUTE EXAMPLE
@router.get('/protected', status_code=status.HTTP_200_OK, tags=["Protected"])
async def protected_route(current_user: dict = Depends(get_current_user)):
    """
    This is an example of a protected route that requires authentication.
    The get_current_user dependency will validate the JWT token and extract user information.
    """
    return {
        "message": "You've accessed a protected route",
        "user_info": current_user
    }