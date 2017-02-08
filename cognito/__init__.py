import boto3
from botocore.exceptions import ClientError
import ast


def attribute_dict(attributes):
    """
    :param attribute_dict: Dictionary of User Pool attribute names/values
    :return: list of User Pool attribute formatted dicts: {'Name': <attr_name>, 'Value': <attr_value>}
    """
    return [{'Name': key, 'Value': value} for key, value in attributes.items()]


class UserObj(object):

    def __init__(self,username, attribute_list):
        self.username = username
        self.pk = username
        for a in attribute_list:
            name = a.get('Name')
            value = a.get('Value')
            if value in ['true','false']:
                value = ast.literal_eval(value.capitalize())
            setattr(self,name,value)


class User(object):

    def __init__(
            self, user_pool_id, client_id,
            username=None, password=None,
            access_key=None, secret_key=None,
            extra_fields=[]):
        """
        :param user_pool_id: Cognito User Pool ID
        :param client_id: Cognito User Pool Application client ID
        :param username: User Pool username
        :param password: User Pool password
        :param access_key: AWS IAM access key
        :param secret_key: AWS IAM secret key
        :param extra_fields:
        """
        if not ((username and password) or (access_key and secret_key)):
            raise ValueError('Must have either username+password or access_key+secret_key')
        self.user_pool_id = user_pool_id
        self.client_id = client_id
        self.username = username
        self.password = password
        self.id_token = None
        self.access_token = None
        self.refresh_token = None
        self.token_type = None
        self.expires_in = None
        if access_key and secret_key:
            self.client = boto3.client('cognito-idp',
                aws_access_key_id=access_key,
                aws_secret_access_key=secret_key,
                )
        else:
            self.client = boto3.client('cognito-idp')

    def register(self, username, password, **kwargs):
        """
        Register the user.
        :param username: User Pool username
        :param password: User Pool password
        :param kwargs: Additional User Pool attributes
        :return response: Response from Cognito

        Example response::
        {
            'UserConfirmed': True|False,
            'CodeDeliveryDetails': {
                'Destination': 'string', # This value will be obfuscated
                'DeliveryMedium': 'SMS'|'EMAIL',
                'AttributeName': 'string'
            }
        }
        """
        user_attrs = [{'Name': key, 'Value': value} for key, value in kwargs.items()]
        response = self.client.sign_up(
            ClientId=self.client_id,
            Username=username,
            Password=password,
            UserAttributes=attribute_dict(kwargs)
        )
        kwargs.update(username=username, password=password)
        self._set_attributes(response, kwargs)

        response.pop('ResponseMetadata')
        return response

    def authenticate(self):
        """
        Authenticate the user.
        :param user_pool_id: User Pool Id found in Cognito User Pool
        :param client_id: App Client ID found in the Apps section of the Cognito User Pool
        :return:

        """

        tokens = self.client.admin_initiate_auth(
            UserPoolId=self.user_pool_id,
            ClientId=self.client_id,
            # AuthFlow='USER_SRP_AUTH'|'REFRESH_TOKEN_AUTH'|'REFRESH_TOKEN'|'CUSTOM_AUTH'|'ADMIN_NO_SRP_AUTH',
            AuthFlow='ADMIN_NO_SRP_AUTH',
            AuthParameters={
                'USERNAME': self.username,
                'PASSWORD': self.password
            },
        )
        self.expires_in = tokens['AuthenticationResult']['ExpiresIn']
        self.id_token = tokens['AuthenticationResult']['IdToken']
        self.refresh_token = tokens['AuthenticationResult']['RefreshToken']
        self.access_token = tokens['AuthenticationResult']['AccessToken']
        self.token_type = tokens['AuthenticationResult']['TokenType']

    def update_profile(self, **kwargs):
        """
        Updates User attributes
        """
        user_attrs = attribute_dict(kwargs)
        response = self.client.update_user_attributes(
            UserAttributes=user_attrs,
            AccessToken='string'
        )
        self._set_attributes(response, kwargs)

    def get_user(self):
        """
        Get the user's details
        :param user_pool_id: The Cognito User Pool Id
        :return: UserObj object
        """
        return UserObj(self.username,
                       self.client.admin_get_user(
                           UserPoolId=self.user_pool_id,
                           Username=self.username).get('UserAttributes'))

    def initiate_change_password(self):
        """
        Resets user's password as an admin
        Message is sent via Verification method set in User Pool(email|phone)
        that includes password reset link 
        WARNING: This invalidates User's current password
        """
        self.client.admin_reset_user_password(
            UserPoolId=self.user_pool_id,
            Username=self.username
        )

    def send_verification(self, attribute='email'):
        """
        Sends the user an attribute verification code for the specified attribute name.
        :param attribute: Attribute to confirm
        """
        self.client.get_user_attribute_verification_code(
            AccessToken=self.access_token,
            AttributeName=attribute
        )

    def validate_verification(self, confirmation_code, attribute='email'):
        """
        Verifies the specified user attributes in the user pool.
        :param confirmation_code: Code sent to user upon intiating verification
        :param attribute: Attribute to confirm 
        """
        self.client.verify_user_attribute(
            AccessToken=self.access_token,
            AttributeName=attribute,
            Code=confirmation_code
        )

    def renew_access_token(self):
        """
        Sets a new access token on the User using the refresh token.
        """
        refresh_response = self.client.admin_initiate_auth(
            UserPoolId=self.user_pool_id,
            ClientId=self.client_id,
            AuthFlow='REFRESH_TOKEN',
            AuthParameters={
                'REFRESH_TOKEN': self.refresh_token
            },
        )
        self._set_attributes(
            refresh_response,
            {
                'access_token': refresh_response['AuthenticationResult']['AccessToken']
            }
        )

    def initiate_forgot_password(self):
        """
        Sends a verification code to the user to use to change their password.
        """
        self.client.forgot_password(
            ClientId=self.client_id,
            Username=self.username
        )

    def confirm_forgot_password(self, confirmation_code, password):
        """
        Allows a user to enter a code provided when they reset their password 
        to update their password.
        :param confirmation_code: The confirmation code sent by a user's request 
        to retrieve a forgotten password
        :param password: New password
        """
        response = self.client.confirm_forgot_password(
            ClientId=self.client_id,
            Username=self.username,
            ConfirmationCode=confirmation_code,
            Password=password
        )
        self._set_attributes(response, {'password': password})

    def change_password(self, previous_password, proposed_password):
        """
        Change the User password
        """
        response = self.client.change_password(
            PreviousPassword=previous_password,
            ProposedPassword=proposed_password,
            AccessToken=self.access_token
        )
        self._set_attributes(response, {'password': password})

    def _set_attributes(self, response, attribute_dict):
        """
        Set user attributes based on response code
        :param response: HTTP response from Cognito
        :attribute dict: Dictionary of attribute name and values
        """
        if response['HTTPStatusCode'] == 200:
            for k, v in attribute_dict.items():
                self.setattr(k, v)