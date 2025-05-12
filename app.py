import chainlit as cl
# Original code being restored:
from langchain_google_community import CalendarCreateEvent
from langchain.agents import AgentExecutor, create_openai_functions_agent
from langchain_core.prompts import ChatPromptTemplate
from langchain_openai import AzureChatOpenAI
import os
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

# In-memory store for user tokens (access_token indexed by a user identifier)
user_tokens = {}

@cl.oauth_callback
async def oauth_callback(
    provider_id: str,
    token: str, # This is the access token from Google
    raw_user_data: dict,
    default_user: cl.User, 
) -> cl.User | None: 
    print(f"OAuth Callback: provider_id={provider_id}")
    print(f"OAuth Callback: default_user object: {default_user}")
    try:
        print(f"OAuth Callback: dir(default_user): {dir(default_user)}")
        print(f"OAuth Callback: default_user.__dict__: {default_user.__dict__}")
    except Exception as e:
        print(f"OAuth Callback: Error inspecting default_user: {e}")
    print(f"OAuth Callback: raw_user_data: {raw_user_data}")

    user_identifier_for_token_storage = None

    if hasattr(default_user, 'identifier') and default_user.identifier:
        user_identifier_for_token_storage = default_user.identifier
        print(f"OAuth Callback: Using default_user.identifier: {user_identifier_for_token_storage}")
    elif raw_user_data.get('id'): # Google's own ID for the user
        user_identifier_for_token_storage = raw_user_data.get('id')
        print(f"OAuth Callback: Using raw_user_data['id']: {user_identifier_for_token_storage}")
    elif raw_user_data.get('email'):
        user_identifier_for_token_storage = raw_user_data.get('email')
        print(f"OAuth Callback: Using raw_user_data['email']: {user_identifier_for_token_storage}")
    else:
        print("OAuth Callback Error: Could not determine a unique user identifier from default_user or raw_user_data.")
        # If no identifier, we can't store the token mapped to a user.
        # Depending on policy, might return None to indicate login failure.
        return None 

    if user_identifier_for_token_storage and provider_id == "google":
        # Store the token in our in-memory dict, mapped to the user.
        user_tokens[user_identifier_for_token_storage] = token
        print(f"OAuth Callback: Stored Google access token in user_tokens for user: {user_identifier_for_token_storage}")
        # IMPORTANT: Do NOT try to set cl.user_session here as context might not be ready.
        # Chainlit will use the returned default_user to establish the session.
    elif provider_id == "google":
        print("OAuth Callback: Could not store Google token in user_tokens as no suitable user identifier was found.")
        return None # Indicate login failure

    return default_user # Return the authenticated user to Chainlit

@cl.on_chat_start
async def on_chat_start():
    print("on_chat_start: Entered")
    # Check for Azure OpenAI environment variables (essential for LLM)
    required_env_vars = [
        "AZURE_OPENAI_API_KEY",
        "AZURE_OPENAI_ENDPOINT",
        "AZURE_OPENAI_DEPLOYMENT_NAME",
    ]
    missing_vars = [var for var in required_env_vars if not os.getenv(var)]
    if missing_vars:
        await cl.Message(
            content=f"Server configuration error: The following Azure OpenAI environment variables are missing: {', '.join(missing_vars)}. Please contact the administrator."
        ).send()
        print(f"on_chat_start: Missing Azure OpenAI env vars: {missing_vars}")
        return

    # Attempt to get the current authenticated user from the session
    # Chainlit should populate this after a successful oauth_callback
    current_user = cl.user_session.get("user") 
    
    if not current_user:
        # This can happen if OAuth hasn't completed or failed silently before on_chat_start
        # Or if the user is accessing without having gone through OAuth (e.g. if auth is not strictly enforced on all paths)
        print("on_chat_start: No current_user in session. Prompting for Google Sign-In.")
        await cl.Message(content="Please sign in with Google to use the Calendar features.").send()
        return

    print(f"on_chat_start: current_user from session: {current_user}")
    user_identifier = None
    if hasattr(current_user, 'identifier') and current_user.identifier:
        user_identifier = current_user.identifier
        print(f"on_chat_start: User identifier from current_user.identifier: {user_identifier}")
    else:
        # Fallback: If the 'user' object in session doesn't have 'identifier',
        # this indicates an issue or a different structure than expected.
        # We previously stored raw_user_data['id'] or ['email'] as identifier.
        # However, without knowing which one was used, it's hard to look up in user_tokens here.
        # The default_user.identifier from oauth_callback should be the primary key.
        print("on_chat_start: current_user in session does not have an 'identifier' attribute. Cannot retrieve token.")
        await cl.Message(content="Error: Could not identify user session to retrieve calendar token. Please try signing in again.").send()
        return

    # Try to retrieve the token from our in-memory store using the user's identifier
    retrieved_google_token = user_tokens.get(user_identifier)

    if retrieved_google_token:
        print(f"on_chat_start: Found token in user_tokens for {user_identifier}. Setting it in cl.user_session.")
        cl.user_session.set("google_access_token", retrieved_google_token)
    else:
        # Token not found in our store, or user_identifier didn't match.
        # This implies something went wrong after oauth_callback or user not fully authenticated.
        print(f"on_chat_start: Google token not found in user_tokens for identifier: {user_identifier}. Prompting for sign-in.")
        # It's possible that the user authenticated but the token wasn't stored, or this is a new session start
        # where the oauth_callback hasn't populated user_tokens for this identifier yet.
        # For robustness, we ensure the session has the token. If not, they need to go through the flow
        # that populates user_tokens via oauth_callback.
        await cl.Message(content="Your Google session seems to be incomplete. Please try signing in with Google again.").send()
        return # Stop further processing as we don't have the token in session.

    # Now check if google_access_token is actually in the session after attempting to set it
    google_access_token_in_session = cl.user_session.get("google_access_token")
    if not google_access_token_in_session:
        # This would be unusual if the above set call was expected to work and token was found
        print("on_chat_start: google_access_token still not in session after attempting to set. Prompting for sign-in.")
        await cl.Message(content="Failed to initialize Google session. Please sign in with Google.").send()
        return
        
    print("on_chat_start: Google access token successfully set in session. Ready for calendar operations.")
    await cl.Message(content="I'm ready! How can I help you manage your calendar? (e.g., 'Schedule a meeting for tomorrow at 10am')").send()


@cl.on_message
async def on_message(message: cl.Message):
    print(f"on_message: Received message: '{message.content}'")
    google_access_token = cl.user_session.get("google_access_token")

    if not google_access_token:
        print("on_message: No google_access_token in session. Prompting to sign in.")
        await cl.Message(content="Please sign in with Google first.").send()
        return

    # Verify Azure OpenAI environment variables again before use
    azure_openai_api_key = os.getenv("AZURE_OPENAI_API_KEY")
    azure_openai_endpoint = os.getenv("AZURE_OPENAI_ENDPOINT")
    azure_openai_deployment_name = os.getenv("AZURE_OPENAI_DEPLOYMENT_NAME")
    openai_api_version = os.getenv("OPENAI_API_VERSION", "2024-02-01") 

    if not all([azure_openai_api_key, azure_openai_endpoint, azure_openai_deployment_name]):
        print("on_message: Azure OpenAI service not configured properly.")
        await cl.Message(content="Azure OpenAI service is not configured properly. Please contact an administrator.").send()
        return

    try:
        print("on_message: Creating Google Credentials object.")
        credentials = Credentials(token=google_access_token)

        try:
            print("on_message: Building Google Calendar API service.")
            calendar_service = build('calendar', 'v3', credentials=credentials)
        except Exception as e:
            error_msg = f"Failed to build Google Calendar service: {str(e)}. Ensure your OAuth token has the required calendar scope."
            print(f"on_message: {error_msg}")
            await cl.Message(content=error_msg).send()
            return
            
        print("on_message: Initializing CalendarCreateEvent tool.")
        calendar_tool = CalendarCreateEvent(api_resource=calendar_service)
        
        print("on_message: Initializing AzureChatOpenAI LLM.")
        llm = AzureChatOpenAI(
            azure_endpoint=azure_openai_endpoint,
            api_version=openai_api_version,
            deployment_name=azure_openai_deployment_name,
            api_key=azure_openai_api_key,
            temperature=0,
        )
        
        tools = [calendar_tool]

        prompt = ChatPromptTemplate.from_messages(
            [
                ("system", "You are a helpful assistant that can create calendar events. Use the provided calendar tool to create events based on the user's request. Confirm the event creation with the event ID or a natural language confirmation. If you cannot create the event, explain why."),
                ("human", "{input}"),
                ("placeholder", "{agent_scratchpad}"),
            ]
        )

        print("on_message: Creating OpenAI functions agent.")
        agent = create_openai_functions_agent(llm, tools, prompt)
        agent_executor = AgentExecutor(agent=agent, tools=tools, verbose=True, handle_parsing_errors=True)

        print(f"on_message: Invoking agent with input: '{message.content}'.")
        await cl.Message(content=f"Processing your request: '{message.content}'...").send()
        response = await agent_executor.ainvoke({"input": message.content})
        
        output_message = response.get("output", "No output from agent.")
        print(f"on_message: Agent response: {output_message}")
        await cl.Message(content=output_message).send()

    except Exception as e:
        error_message = f"An error occurred: {str(e)}"
        print(f"on_message: Exception: {error_message}")
        await cl.Message(content=error_message).send() 
