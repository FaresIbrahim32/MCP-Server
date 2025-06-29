#main.py
from mcp.server.fastmcp import FastMCP
from dotenv import load_dotenv
import httpx
from pydantic import BaseModel
from pydantic_settings import BaseSettings, SettingsConfigDict
from typing import List
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from datetime import datetime, timedelta
from typing import Optional
import json
import os

load_dotenv()

API_KEY = os.getenv('TICKETMASTER_CONSUMER_KEY')
BASE_URL = "https://app.ticketmaster.com/discovery/v2/events.json"

# Create an MCP server
mcp = FastMCP(
    "TicketMaster MCP",
    port=6599,  # Explicit port
    expose_tools_route=True,  # Critical for Cursor
    enable_remote_tools=True  # Allows Cursor to access tools
)

class SurgeSettings(BaseSettings):
    model_config: SettingsConfigDict = SettingsConfigDict(
        env_prefix="SURGE_", 
        env_file=".env",
        extra='ignore'  # This fixes the validation error!
    )
        
    api_key: str
    account_id: str
    my_phone_number: str
    my_first_name: str
    my_last_name: str

# Create settings instance once at module level
surge_settings = SurgeSettings()

@mcp.tool()
def text_me_my_event(message: str) -> str:
    """This is a tool that takes a prompt for a user who is looking to book events based on his/her hobby"""
    # For now, just send the message - you can add event booking logic here
    return textme(message)

@mcp.tool(name="textme", description="Send a text message to me")
def textme(text_content: str) -> str:
    """Send a text message to a phone number via https://surgemsg.com/"""
    try:
        with httpx.Client() as client:
            response = client.post(
                "https://api.surge.app/messages",  # Fixed URL
                headers={
                    "Authorization": f"Bearer {surge_settings.api_key}",
                    "Surge-Account": surge_settings.account_id,
                    "Content-Type": "application/json",
                },
                json={
                    "body": text_content,
                    "conversation": {
                        "contact": {
                            "first_name": surge_settings.my_first_name,
                            "last_name": surge_settings.my_last_name,
                            "phone_number": surge_settings.my_phone_number,
                        }
                    },
                },
            )
            response.raise_for_status()
            return f"Message sent successfully: {text_content}"
    except httpx.HTTPStatusError as e:
        return f"Error sending message: {e.response.status_code} - {e.response.text}"
    except Exception as e:
        return f"Error sending message: {str(e)}"

@mcp.tool(name="searchevents", description="Search for events using Ticketmaster API")

def search_events(keyword: str) -> str:
    """Search for events using the Ticketmaster API"""
    url = f"{BASE_URL}?size=10&keyword={keyword}&apikey={API_KEY}"
        
    try:
        with httpx.Client() as client:
            response = client.get(url)
            response.raise_for_status()
                
            data = response.json()
                
            events_list = []
                
            # Extract events from response
            if '_embedded' in data and 'events' in data['_embedded']:
                for event in data['_embedded']['events']:
                    name = event.get('name', 'Unknown Event')
                    date = event.get('dates', {}).get('start', {}).get('localDate', 'TBD')
                    time = event.get('dates', {}).get('start', {}).get('localTime', '')
                    venue = event.get('_embedded', {}).get('venues', [{}])[0].get('name', 'Unknown Venue')
                    url = event.get('url', '')
                    
                    # Format: "Event Name | Date at Time | Venue | URL"
                    time_part = f" at {time}" if time else ""
                    event_info = f"{name} | {date}{time_part} | {venue} | {url}"
                    events_list.append(event_info)
            else:
                events_list.append("No events found")
                
            return "\n".join(events_list)
            
    except httpx.HTTPStatusError as e:
        return f"Error searching events: {e.response.status_code} - {e.response.text}"
    except Exception as e:
        return f"Error searching events: {str(e)}"


# If modifying these scopes, delete the file token.json.
SCOPES = ['https://www.googleapis.com/auth/calendar']

class GoogleCalendarManager:
    def __init__(self):
        self.service = None
        self.authenticate()
    
    def authenticate(self):
        """Authenticate with Google Calendar API"""
        creds = None
        
        # The file token.json stores the user's access and refresh tokens.
        if os.path.exists('token.json'):
            creds = Credentials.from_authorized_user_file('token.json', SCOPES)
        
        # If there are no (valid) credentials available, let the user log in.
        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                creds.refresh(Request())
            else:
                # You need to create credentials.json from Google Cloud Console
                flow = InstalledAppFlow.from_client_secrets_file(
                    'client_secret.json', SCOPES)
                creds = flow.run_local_server(port=0)
            
            # Save the credentials for the next run
            with open('token.json', 'w') as token:
                token.write(creds.to_json())
        
        self.service = build('calendar', 'v3', credentials=creds)
    
    def create_event(self, title, start_datetime, end_datetime=None, 
                    description=None, location=None, timezone='America/New_York'):
        """Create a calendar event"""
        
        if not end_datetime:
            # Default to 2 hours if no end time specified
            end_datetime = start_datetime + timedelta(hours=2)
        
        # Format datetime for Google Calendar API
        start_time = start_datetime.isoformat()
        end_time = end_datetime.isoformat()
        
        event = {
            'summary': title,
            'location': location,
            'description': description,
            'start': {
                'dateTime': start_time,
                'timeZone': timezone,
            },
            'end': {
                'dateTime': end_time,
                'timeZone': timezone,
            },
            'reminders': {
                'useDefault': False,
                'overrides': [
                    {'method': 'email', 'minutes': 24 * 60},  # 1 day before
                    {'method': 'popup', 'minutes': 60},       # 1 hour before
                ],
            },
        }
        
        try:
            event_result = self.service.events().insert(
                calendarId='primary', 
                body=event
            ).execute()
            
            return {
                'success': True,
                'event_id': event_result.get('id'),
                'html_link': event_result.get('htmlLink'),
                'message': f"Event '{title}' created successfully"
            }
            
        except HttpError as error:
            return {
                'success': False,
                'error': str(error),
                'message': f"Failed to create event: {error}"
            }

# Initialize the calendar manager
calendar_manager = GoogleCalendarManager()    
@mcp.tool(
    name="save_ticketmaster_event",
    description="Save a Ticketmaster event to Google Calendar with automatic parsing"
)
def save_ticketmaster_event(event_info: str) -> str:
    """
    Save a Ticketmaster event to Google Calendar
    Automatically parses event information from Ticketmaster search results
    
    Args:
        event_info: Event information string from Ticketmaster search
                   Format: "Event Name | Date | Venue | URL"
    
    Returns:
        Success/failure message
    """
    
    try:
        # Parse the event info string
        parts = event_info.split(" | ")
        
        if len(parts) < 3:
            return "❌ Invalid event format. Expected: 'Event Name | Date | Venue | URL'"
        
        title = parts[0].strip()
        date_part = parts[1].strip()
        venue = parts[2].strip() if len(parts) > 2 else None
        url = parts[-1].strip() if "http" in parts[-1] else None
        
        # Extract date (assumes format like "2025-08-15 at 19:30" or just "2025-08-15")
        if " at " in date_part:
            date_str, time_str = date_part.split(" at ")
            time_str = time_str.strip()
        else:
            date_str = date_part
            time_str = "19:00"  # Default to 7 PM if no time specified
        
        # Handle different time formats
        if ":" not in time_str:
            time_str = "19:00"  # Default time
        
        # Create description with URL if available
        description = f"Ticketmaster Event"
        if url:
            description += f"\nTickets: {url}"
        
        # Create the calendar event
        event_datetime = datetime.strptime(f"{date_str} {time_str}", "%Y-%m-%d %H:%M")
        end_datetime = event_datetime + timedelta(hours=3)  # Most events are ~3 hours
        
        result = calendar_manager.create_event(
            title=title,
            start_datetime=event_datetime,
            end_datetime=end_datetime,
            description=description,
            location=venue
        )
        
        if result['success']:
            return f"🎫 Ticketmaster event saved to calendar!\n{title}\n📅 {date_str} at {time_str}\n📍 {venue}\nView: {result['html_link']}"
        else:
            return f"❌ Failed to save event: {result['message']}"
            
    except Exception as e:
        return f"❌ Error parsing/saving event: {str(e)}"
    
#events = search_events("Club World Cup")

# Save the first event to calendar
#if events:
    #calendar_result = save_ticketmaster_event(events[0])
    #print(calendar_result)
