from fastapi import APIRouter, Depends, HTTPException, status
from typing import List, Optional

from core.dependencies import get_current_user, get_aws_cognito
from core.aws_cognito import AWS_Cognito

router = APIRouter(
    prefix="/protected",
    tags=["Protected"],
    dependencies=[Depends(get_current_user)]  # All routes in this router are protected
)

@router.get("/profile", status_code=status.HTTP_200_OK)
async def get_user_profile(current_user: dict = Depends(get_current_user)):
    """
    Get the current user's profile information.
    This endpoint is protected and requires a valid JWT token.
    """
    return {
        "message": "Profile retrieved successfully",
        "profile": current_user
    }

@router.get("/admin", status_code=status.HTTP_200_OK)
async def admin_only(current_user: dict = Depends(get_current_user)):
    """
    Admin-only endpoint.
    This demonstrates role-based access control with JWT tokens.
    """
    # Check if the user has the 'admin' role/group
    if 'admin' not in current_user.get('groups', []):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You don't have access to this resource"
        )
    
    return {
        "message": "Welcome, admin!",
        "admin_info": "This is sensitive admin information"
    }

@router.get("/dashboard", status_code=status.HTTP_200_OK)
async def user_dashboard(current_user: dict = Depends(get_current_user)):
    """
    User dashboard endpoint.
    This is a protected endpoint that any authenticated user can access.
    """
    return {
        "message": "Welcome to your dashboard",
        "user": current_user['username'],
        "dashboard_data": {
            "items": ["item1", "item2", "item3"],
            "stats": {
                "visits": 42,
                "actions": 12
            }
        }
    }
