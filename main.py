from fastapi import FastAPI, File, Request, UploadFile, HTTPException, Depends, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
import google.oauth2.id_token
from google.auth.transport import requests
from google.cloud import firestore, storage
from google.cloud.firestore_v1.base_query import FieldFilter
import starlette.status as status
from starlette.status import HTTP_401_UNAUTHORIZED, HTTP_303_SEE_OTHER
from datetime import datetime
import local_constants

app = FastAPI()

# Firebase and Firestore setup
firestore_db = firestore.Client()
firebase_request_adapter = requests.Request()

# Constants
USER_COLLECTION = "User"
TWEET_COLLECTION = 'Tweet'

MAIN_TEMPLATE = "main.html"
UPDATE_PROFILE_TEMPLATE = "update_profile.html"
USER_INFORMATION_TEMPLATE = "user_information.html"

# Mount static directory
app.mount("/static", StaticFiles(directory="static"), name="static")

# Templating
templates = Jinja2Templates(directory="templates")


"""
A function that retrieves a user's document from the Firestore database based on the provided user token.

Args:
    user_token (dict): A dictionary containing the user's ID.

Returns:
    firestore.DocumentReference: The reference to the user's document in the Firestore database.
"""
def get_user(user_token):
    user = firestore_db.collection(USER_COLLECTION).document(user_token['user_id'])
    return user

"""
Validates a Firebase ID token.

Args:
    id_token (str): The Firebase ID token to be validated.

Returns:
    dict or None: The decoded token if it is valid, None otherwise.
"""
def validate_firebase_token(id_token):
    if not id_token:
        return None
    try:
        return google.oauth2.id_token.verify_firebase_token(id_token, firebase_request_adapter)
    except ValueError as err:
        print(str(err))
        return None

"""
Get the current user based on the request object.

Parameters:
    - request (Request): The request object containing user information.

Returns:
    - Tuple: A tuple containing the user object and the user token.
""" 
def get_current_user(request: Request):
    id_token = request.cookies.get("token")
    user_token = None
    user = None

    user_token = validate_firebase_token(id_token)
    if not user_token:
        return None, None

    user = get_user(user_token)

    return user, user_token

"""
Get the root page of the application.

This function is a route handler for the root URL ("/") of the application. It handles the GET request and returns the root page of the application.

Parameters:
    - request (Request): The incoming request object.

Returns:
    - TemplateResponse: The rendered HTML template for the root page.
"""
@app.get("/", response_class=HTMLResponse)
async def root(request: Request):
    error_message = "No error here"

    user, user_token = get_current_user(request)

    if user is None:
        return templates.TemplateResponse("main.html", {"request": request, 'user_token': None, 'error_message': None, 'user_info': None})
    
    user_doc = user.get()

    if not user_doc.exists:
       return RedirectResponse("/set_username", status_code=status.HTTP_303_SEE_OTHER)

    user_info = user_doc.to_dict()

    timeline_tweets = get_timeline_tweets_by_chronological_order(user, user_info)

    return templates.TemplateResponse("main.html", {"request": request, 'user_token': user_token, 'error_message': error_message, 'user_info': user_info, "timeline_tweets": timeline_tweets})

"""
Handles the POST request to add a new tweet.

Args:
    request (Request): The incoming request object.
    tweet (str, optional): The content of the tweet. Defaults to Form(...).
    image (UploadFile, optional): The image file for the tweet. Defaults to File(None).

Returns:
    RedirectResponse: A redirect response to the home page after adding the tweet.

Raises:
    RedirectResponse: If the user does not exist, redirects to the set_username page.

Side Effects:
    Updates the tweet data in the database.

"""
@app.post("/tweet")
async def add_tweet(request: Request, tweet: str = Form(...), image: UploadFile = File(...)):
    user, user_token = get_current_user(request)
    user_doc = user.get() 

    if not user_doc.exists:
        return RedirectResponse("/set_username", status_code=status.HTTP_303_SEE_OTHER)

    image_url = ""
    if image and image.filename:
        image_url = await upload_file_handler(image)

    username = user_doc.to_dict()['username']

    tweet_data = {
        'name': tweet,
        'username': username,
        'date': datetime.now(),
        'image_url': image_url
    }
    tweet_ref = firestore_db.collection(USER_COLLECTION).document(user_token['user_id']).collection(TWEET_COLLECTION).document()
    tweet_ref.set(tweet_data)

    return RedirectResponse(url="/", status_code=status.HTTP_303_SEE_OTHER)

"""
A route handler for the "/set_username" endpoint.

This function handles the GET request to the "/set_username" endpoint. It returns an HTML response with a template that allows the user to set their username. The template is rendered using the "UPDATE_PROFILE_TEMPLATE" template and includes the request object and an empty message.

Parameters:
    request (Request): The incoming request object.

Returns:
    TemplateResponse: The rendered HTML template with the update profile page.
"""
@app.get("/set_username", response_class=HTMLResponse)
async def set_username(request: Request):
    message = ""
    return templates.TemplateResponse(UPDATE_PROFILE_TEMPLATE, {"request": request, "message": message})

"""
Search for users with a username that starts with the given prefix.

Parameters:
    - request (Request): The HTTP request object.
    - prefix (str): The prefix to search for in the username.

Returns:
    - TemplateResponse: The rendered HTML template with the search results.
"""
@app.post("/search_username")
async def search_username(request: Request, name: str = Form(...)):
    error_message = "No error here"
    user, user_token = get_current_user(request)
    user_info = user.get().to_dict()

    # Search for users that start with the given prefix
    user_query = firestore_db.collection(USER_COLLECTION).where(filter=FieldFilter('username', '>=', name)).where(filter=FieldFilter('username', '<', name + u'\uf8ff')).limit(10)
    users = [doc.to_dict() for doc in user_query.stream()]

    # Get the timeline tweets by chronological order
    timeline_tweets = get_timeline_tweets_by_chronological_order(user, user_info)

    return templates.TemplateResponse(MAIN_TEMPLATE, {"request": request, 'user_token': user_token, 'error_message': error_message, 'user_info': user_info, 'users_found': users, "name": name, "timeline_tweets": timeline_tweets})

"""
Save the provided username for the current user.
Parameters:
    - request (Request): The HTTP request object.
    - username (str): The username to be saved.
Returns:
    - RedirectResponse: A redirect response to the home page.
Raises:
    - HTTPException: If the user is not authorized.
Example Usage:
    @app.post("/save_username", response_class=RedirectResponse)
    async def save_username(request: Request, username: str = Form(...)):
        ...
"""
@app.post("/save_username", response_class=RedirectResponse)
async def save_username(request: Request, username: str = Form(...)):
    id_token = request.cookies.get("token")
    user_token = validate_firebase_token(id_token)
    if not user_token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unauthorized")
    
    # Check if the username is already taken
    user_ref = firestore_db.collection(USER_COLLECTION).where("username", "==", username).stream()
    if any(user.exists for user in user_ref):
        return templates.TemplateResponse(UPDATE_PROFILE_TEMPLATE, {"request": request, "message": "Username already taken"})

    user_ref = firestore_db.collection(USER_COLLECTION).document(user_token['user_id'])

    if not user_ref.get().exists:
        user_data = {
            'username': username
        }
        user_ref.set(user_data)

    return RedirectResponse(url="/", status_code=status.HTTP_303_SEE_OTHER)

"""
Retrieves tweets from the database that match the given search words.

Parameters:
    - request (Request): The request object for the HTTP request.
    - words (str): The search words to match against the tweet names.

Returns:
    - TemplateResponse: The rendered HTML template with the search results, including the request, user token, user information, search words, and found tweets.
"""
@app.post("/search_tweets")
async def search_tweets(request: Request, words: str = Form(...)):
    tweets_found = []
    
    user, user_token = get_current_user(request)
    user_info = user.get().to_dict()

    users = firestore_db.collection(USER_COLLECTION).stream()

    for user in users:
        user_id = user.id

        # Search for tweets that start with the given prefix
        tweets_query = firestore_db.collection(USER_COLLECTION).document(user_id).collection(TWEET_COLLECTION).where(filter=FieldFilter('name', '>=', words)).where(filter=FieldFilter('name', '<=', words + '\uf8ff')).stream()
        # Get the timeline tweets by chronological order
        tweets_found.extend([doc.to_dict() for doc in tweets_query])

    return templates.TemplateResponse(MAIN_TEMPLATE, {"request": request, 'user_token': user_token, 'user_info': user_info, "words": words, 'tweets': tweets_found})

"""
Retrieves the profile information of a user with the given username.

Parameters:
    - request (Request): The request object for the HTTP request.
    - username (str): The username of the target user.

Returns:
    - TemplateResponse: The rendered HTML template with the user's profile information, including their tweets and follower status.

Raises:
    - HTTPException: If the user with the given username is not found.
"""
@app.get("/profile/{username}", response_class=HTMLResponse)
async def set_username(request: Request, username: str):
    user_docs = firestore_db.collection(USER_COLLECTION).where('username', '==', username).limit(1).get()
    if not user_docs:
        raise HTTPException(status_code=404, detail="User not found")

    user_doc = user_docs[0]

    # Get the user's tweets
    tweets_query = user_doc.reference.collection(TWEET_COLLECTION).order_by('date', direction='DESCENDING').limit(10).stream()
    tweets = [tweet.to_dict() for tweet in tweets_query]

    is_following_user = True
    user_info = user_doc.to_dict()

    user, _ = get_current_user(request)

    if user:
        current_user_doc = user.get()

        if current_user_doc.exists:
            current_username = current_user_doc.get('username')
            target_user_followers = user_doc.to_dict().get('followers', [])
            is_following_user = current_username in target_user_followers

    return templates.TemplateResponse(USER_INFORMATION_TEMPLATE, {"request": request, "user_info": user_info, "tweets": tweets, "is_following_user": is_following_user, 'username': username})

"""
Function to follow a user. Checks if the current user can follow the target user, updates followers list accordingly,
and redirects to the profile of the target user. 

Parameters:
- request: Request object for the HTTP request
- username: The username of the target user to follow

Returns:
- RedirectResponse: Redirects to the profile of the target user if successful
- Raises HTTPException with status code 400 if the current user tries to follow themselves
- Raises HTTPException with status code 404 if the target user is not found
"""
@app.post("/follow/{username}")
async def follow(request: Request, username: str):
    user, _ = get_current_user(request)
    
    user = user.get()
    current_username = user.to_dict().get('username')

    if current_username == username:
        raise HTTPException(status_code=400, detail="Bad Request: Cannot follow yourself")

    # Check if the target user exists
    target_users = firestore_db.collection(USER_COLLECTION).where(filter=FieldFilter('username', '==', username)).limit(1).get()
    if not target_users:
        raise HTTPException(status_code=404, detail="Not Found: Target user not found")

    target_user = target_users[0]

    # Update the followers list
    target_user_ref = target_user.reference
    target_user_followers = target_user.to_dict().get('followers', [])
    if current_username not in target_user_followers:
        target_user_followers.append(current_username)
        target_user_ref.update({"followers": target_user_followers})

    # Update the following list
    current_user_following = user.to_dict().get('following', [])
    if username not in current_user_following:
        current_user_following.append(username)
        user.reference.update({"following": current_user_following})

    return RedirectResponse(url=f"/profile/{username}", status_code=status.HTTP_303_SEE_OTHER)

"""
A function that handles the unfollow action for a user. It checks if the current user can unfollow the target user, updates the followers and following lists accordingly, and redirects to the target user's profile.

Args:
    request: The request object.
    username: The username of the target user to unfollow.

Returns:
    RedirectResponse: Redirects to the target user's profile after unfollowing.
"""
@app.post("/unfollow/{username}")
async def unfollow_user(request: Request, username: str):
    user, _ = get_current_user(request)
    
    current_user_doc = user.get()
    current_username = current_user_doc.to_dict().get('username')

    if current_username == username:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Bad Request: Cannot follow yourself")

    target_user_ref = firestore_db.collection(USER_COLLECTION).where(filter=FieldFilter('username', '==', username)).limit(1).get()
    if not target_user_ref:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Target user not found")

    target_user_doc = target_user_ref[0]

    # Update the followers list
    target_user_followers = target_user_doc.to_dict().get('followers', [])
    if current_username in target_user_followers:
        target_user_followers.remove(current_username)
        target_user_doc.reference.update({"followers": target_user_followers})

    # Update the following list
    current_user_following = current_user_doc.to_dict().get('following', [])
    if username in current_user_following:
        current_user_following.remove(username)
        current_user_doc.reference.update({"following": current_user_following})

    return RedirectResponse(url=f"/profile/{username}", status_code=status.HTTP_303_SEE_OTHER)

"""
A function that retrieves timeline tweets by chronological order for a given user.

Args:
    user: The user object for whom the timeline tweets are fetched.
    user_info: Information about the user, including following list.

Returns:
    List: A list of timeline tweets sorted by date in descending order.
"""
def get_timeline_tweets_by_chronological_order(user, user_info):
    following_list = user_info.get('following', [])
    tweets = []

    # Get the timeline tweets from each user in the following list
    for username in following_list:
        user_docs = firestore_db.collection(USER_COLLECTION).where(filter=FieldFilter('username', '==', username)).get()
        for user_doc in user_docs:
            user_tweets_query = user_doc.reference.collection(TWEET_COLLECTION).order_by('date', direction='DESCENDING').limit(20).stream()
            user_tweets = [{"id": tweet.id, **tweet.to_dict()} for tweet in user_tweets_query]
            tweets.extend(user_tweets)

    # Get the timeline tweets from the current user
    user_tweets = user.collection(TWEET_COLLECTION).order_by('date', direction='DESCENDING').limit(20).stream()
    available_tweets = [{"id": tweet.id, **tweet.to_dict()} for tweet in user_tweets]
    tweets.extend(available_tweets)

    # Sort the tweets by date
    timeline_tweets = sorted(tweets, key=lambda x: x['date'], reverse=True)[:20]
    return timeline_tweets

"""
Handles the GET request to edit a tweet.

Args:
    request (Request): The incoming request object.
    tweet_id (str): The ID of the tweet to be edited.

Returns:
    TemplateResponse: The rendered HTML template with the edit tweet page.

Raises:
    RedirectResponse: If the user is not authenticated.

Side Effects:
    Retrieves the tweet with the given ID from the database.

"""
@app.get("/edit/{tweet_id}", response_class=HTMLResponse)
async def edit_tweet_page(request: Request, tweet_id: str):
    user, user_token = get_current_user(request)
    message = request.query_params.get("message", "")

    if not user_token:
        return RedirectResponse(url="/", status_code=status.HTTP_303_SEE_OTHER)
    
    if not user:
        return RedirectResponse(url="/", status_code=status.HTTP_303_SEE_OTHER)
    
    tweet = await get_tweet_by_Id(user, tweet_id)

    if not tweet:
        return {"message": "Tweet not found"}
    
    return templates.TemplateResponse("edit_tweet.html", {"request": request, "tweet": tweet, "tweet_id": tweet_id, "message": message})

"""
Handles the POST request to edit a tweet.

Args:
    request (Request): The incoming request object.
    tweet_id (str): The ID of the tweet to be edited.
    tweet (str, optional): The new content of the tweet. Defaults to Form(...).
    image (UploadFile, optional): The new image for the tweet. Defaults to File(...).

Returns:
    HTMLResponse: A redirect response to the edit tweet page with a success message.

Raises:
    RedirectResponse: If the user is not authenticated.

Side Effects:
    Updates the tweet with the new content and image in the database.

"""
@app.post("/edit/{tweet_id}", response_class=HTMLResponse)
async def edit_tweet_handler(request: Request, tweet_id: str, tweet: str = Form(...), image: UploadFile = File(...)): 
    user, user_token = get_current_user(request)

    if not user_token:
        return RedirectResponse(url="/", status_code=status.HTTP_303_SEE_OTHER)

    if not user:
        return RedirectResponse(url="/", status_code=status.HTTP_303_SEE_OTHER)

    retrived_tweet = await get_tweet_by_Id(user, tweet_id)

    if not retrived_tweet:
        return {"message": "Tweet not found"}

    image_url = None

    # Upload the new image if provided
    if not image_url:
        image_url = retrived_tweet.get('image_url')

    tweet_data = {
        'name': tweet,
        'image_url': image_url
    }

    # Update the tweet in the database
    tweet_ref = user.collection(TWEET_COLLECTION).document(tweet_id)
    tweet_ref.update(tweet_data)

    headers = {"message": "Tweet updated successfully"}
    redirect_url = f"/edit/{tweet_id}?message=Tweet+updated+successfully"
    return RedirectResponse(url=redirect_url, status_code=status.HTTP_303_SEE_OTHER, headers=headers)

"""
Retrieves a tweet by its ID.

Args:
    user (User): The user object representing the owner of the tweet.
    tweet_id (str): The ID of the tweet to retrieve.

Returns:
    dict or None: The tweet data as a dictionary if it exists, otherwise None.
"""
async def get_tweet_by_Id(user, tweet_id: str):
    # Check if the tweet exists
    tweet_data =  user.collection(TWEET_COLLECTION).document(tweet_id).get().to_dict()
    if tweet_data:
        return tweet_data
    else:
        return None

"""
Delete a tweet by its ID.

Parameters:
    - request (Request): The request object.
    - tweet_id (str): The ID of the tweet to be deleted.

Returns:
    - RedirectResponse: A redirect response to the home page if the tweet is successfully deleted.
    - RedirectResponse: A redirect response to the home page if the user is not authenticated.
    - RedirectResponse: A redirect response to the home page if the user is not found.
    - dict: A dictionary with a "message" key indicating that the tweet was not found.
"""
@app.get("/delete/{tweet_id}")
async def delete_tweet_page(request: Request, tweet_id: str):
    user, user_token = get_current_user(request)

    if not user_token:
        return RedirectResponse(url="/", status_code=status.HTTP_303_SEE_OTHER)

    if not user:
        return RedirectResponse(url="/", status_code=status.HTTP_303_SEE_OTHER)

    tweet = await get_tweet_by_Id(user, tweet_id)

    if not tweet:
        return {"message": "Tweet not found"}
    
    # Delete the tweet from the database
    tweet_ref = firestore_db.collection(USER_COLLECTION).document(user_token['user_id']).collection(TWEET_COLLECTION).document(tweet_id)
    tweet_ref.delete()

    return RedirectResponse("/", status_code=status.HTTP_303_SEE_OTHER)

"""
A function that handles the upload of a file.

Parameters:
    - image (UploadFile): The file to be uploaded.

Returns:
    - str: The URL of the uploaded file.

Raises:
    - HTTPException: If the file is not a JPG or PNG image.
    - RedirectResponse: If the filename is empty.

This function checks if the uploaded file has a valid filename and if it is a JPG or PNG image. If the filename is empty, it returns a RedirectResponse to the root URL. If the file is not a JPG or PNG image, it raises an HTTPException with a status code of 400 and a detail message
"""
async def upload_file_handler(image: UploadFile = File(...)):
    if image.filename == '':
        return RedirectResponse('/', status_code=status.HTTP_302_FOUND)

    # Check if the file is a JPG or PNG image
    if not (image.filename.endswith(".jpg") or image.filename.endswith(".png")):
        raise HTTPException(status_code=400, detail="Only JPG and PNG images are allowed")
    
    image_url = upload_file(image)
    return image_url

"""
A function that uploads a file to Google Cloud Storage.

Parameters:
    - file: The file to be uploaded.

Returns:
    - A string representing the URL of the uploaded file.
"""
def upload_file(file):
    # Create a storage client
    storage_client = storage.Client(project=local_constants.PROJECT_NAME)
    bucket = storage_client.bucket(local_constants.PROJECT_STORAGE_BUCKET)

    print(file)

    # Upload the file to the bucket
    blob = storage.Blob(file.filename, bucket)
    blob.upload_from_file(file.file)

    # Get the URL of the uploaded file
    return f"https://storage.cloud.google.com/{bucket.name}/{blob.name}"