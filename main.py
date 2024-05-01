import requests
from datetime import datetime, timedelta
import os
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build
from dotenv import load_dotenv

# Google Calendar API configuration
load_dotenv()
SCOPES = ['https://www.googleapis.com/auth/calendar']
CALENDAR_MAIN_ID = os.getenv('CALENDAR_MAIN_ID')
CALENDAR_TASKS_ID = os.getenv('CALENDAR_TASKS_ID')
START_TIME = '2024-05-13T00:00:00+04:00'

# Trello API configuration
API_KEY = os.getenv('API_KEY')
TOKEN = os.getenv('TOKEN')
LIST_ID = os.getenv('LIST_ID')
ESTIMATE_FIELD_ID = os.getenv('ESTIMATE_FIELD_ID')

base_url = "https://api.trello.com/1/"
cards_url = f"{base_url}lists/{LIST_ID}/cards/?customFieldItems=true"
auth_params = {'key': API_KEY, 'token': TOKEN}

def get_cards_with_estimate():
    response = requests.get(cards_url, params=auth_params)
    cards = response.json()
    for card in cards:
        estimate = 0
        for item in card['customFieldItems']:
            if item['idCustomField'] == ESTIMATE_FIELD_ID:
                try:
                    estimate = int(item['value']['number'])
                except (KeyError, ValueError):
                    print("Error extracting estimate")
        card['estimated_hours'] = estimate
    return cards

def create_event(service, calendar_id, summary, start_time, duration_hours):
    print("Start time: ", start_time)  
    end_time = start_time + timedelta(hours=duration_hours)
    print("End time: ", end_time)
    event = {
        'summary': summary,
        'start': {'dateTime': start_time.isoformat()},
        'end': {'dateTime': end_time.isoformat()}
    }
    created_event = service.events().insert(calendarId=calendar_id, body=event).execute()
    return created_event

def delete_all_events(service, calendar_id, start_time):
    # Convert start_time from string to datetime object if provided
    if start_time:
        start_time = datetime.fromisoformat(start_time)
    
    # Call the Calendar API
    print('Fetching list of events from:', start_time)
    events_result = service.events().list(calendarId=calendar_id, singleEvents=True,
                                          timeMin=start_time.isoformat() if start_time else None,
                                          orderBy='startTime').execute()
    events = events_result.get('items', [])

    if not events:
        print('No upcoming events found after:', start_time)
    else:
        for event in events:
            # Extra check to avoid any time zone issues or API inconsistencies
            event_start = datetime.fromisoformat(event['start'].get('dateTime', event['start'].get('date')))
            if event_start >= start_time:
                print('Deleting event:', event['summary'], 'at', event_start)
                service.events().delete(calendarId=calendar_id, eventId=event['id']).execute()


def authenticate_google_calendar():
    creds = None
    if os.path.exists('token.json'):
        print("Loading credentials from token.json")
        creds = Credentials.from_authorized_user_file('token.json', SCOPES)
    if not creds or not creds.valid:
        print("No valid credentials found, requesting new token")
        flow = Flow.from_client_secrets_file('client_secret_2.json', SCOPES, redirect_uri='http://localhost:1')
        auth_url, _ = flow.authorization_url(prompt='consent')
        print('Please go to this URL: {}'.format(auth_url))
        code = input('Enter the authorization code: ')
        flow.fetch_token(code=code)
        creds = flow.credentials
        with open('token.json', 'w') as token:
            token.write(creds.to_json())
    return creds

def process_trello_cards(cards):
    for card in cards:
        for item in card['customFieldItems']:
            # Check if this item's idCustomField matches our target
            if item['idCustomField'] == ESTIMATE_FIELD_ID:
                # If a match is found, extract the number from the value dictionary
                try:
                    estimate = int(item['value']['number'])
                except (KeyError, ValueError):
                    # Handle cases where the number field is missing or is not an integer
                    print("Error extracting estimate")
                    exit()
                break  # Stop the loop after finding the target field
        card['estimated_hours'] = estimate
    return cards

def update_card_dates(card_id, start_date, end_date):
    # URL for updating a card in Trello
    update_card_url = f"https://api.trello.com/1/cards/{card_id}"
    
    # Update params with start and due dates formatted as ISO strings
    update_params = auth_params.copy()
    update_params.update({'start': start_date.isoformat(), 'due': end_date.isoformat()})
    
    # Sending the PUT request to update the card
    response = requests.put(update_card_url, params=update_params)
    
    # Returning the response as JSON
    return response.json()


def get_next_cards(cards, event_duration):
    
    # initialize lists to store allocated and unallocated cards
    cards_allocated = []
    cards_unallocated = []

    # Convert event_duration from timedelta to hours (assuming it's a timedelta object)
    event_duration_hours = event_duration.total_seconds() / 3600
    
    # initialize event_allocated_duration to store the total duration of allocated hours from the event
    event_allocated_duration = 0

    for card in cards:

        # collecting total duration of the card to card_total_duration
        card_total_duration = card['estimated_hours']

        # Check if the current card can fit within the event duration time        
        if event_allocated_duration + card_total_duration <= event_duration_hours:            
            # yes, it will fit, so add the card to the allocated list            

            cards_allocated.append(card)
            event_allocated_duration += card_total_duration

        else:
            # no, it won't fit, so we need to split the card into two parts
            # calculate the allocatable duration for the card
           
            if len(cards_unallocated) == 0:

                card_allocatable_duration = event_duration_hours - event_allocated_duration
              
                # Create a new card with the allocatable duration to store in allocated list
                allocated_card = card.copy()
                allocated_card['estimated_hours'] = card_allocatable_duration

                # BUG: there are some events added with 0 hours, so we need to filter them out                
                
                if(allocated_card['estimated_hours'] > 0):
                    cards_allocated.append(allocated_card)

                # Modify the current card with the leftover duration and add to unallocated
                leftover_duration = card_total_duration - card_allocatable_duration
                card['estimated_hours'] = leftover_duration

                event_allocated_duration = event_duration_hours

            # Add the modified card to the unallocated list
                cards_unallocated.append(card)
            else:
                cards_unallocated.append(card)
        
    return cards_allocated, cards_unallocated

def main():
    creds = authenticate_google_calendar()
    service = build('calendar', 'v3', credentials=creds)
    apex_events = []
    page_token = None

    cards = get_cards_with_estimate()
    cards = process_trello_cards(cards)

    # ATTENTION: This will delete all events in the tasks calendar, be super careful to pick the right calendar
    delete_all_events(service, CALENDAR_TASKS_ID, START_TIME)    
    
    first_task_occurance_name = ""

    while True:
        events_result = service.events().list(calendarId=CALENDAR_MAIN_ID, timeMin=START_TIME, 
                                              singleEvents=True, orderBy='startTime', pageToken=page_token).execute()        
        for apex_event in events_result.get('items', []):
            
            if apex_event['summary'] == "ApexData":
                print("--------------------")
                apex_start = datetime.fromisoformat(apex_event['start']['dateTime'])
                apex_end = datetime.fromisoformat(apex_event['end']['dateTime'])
                last_end_time = apex_start
                event_duration = apex_end - last_end_time

                print("Event name: ", apex_event['summary'], "Event start time: ", apex_event['start']['dateTime'], "Event duration: ", event_duration)
                allocated_cards, unallocated_cards = get_next_cards(cards, event_duration)                
                    
                for card in allocated_cards:
                    
                    if first_task_occurance_name != card['name']:
                        print("This is a new card name")
                        print("first_task_occurance_name: ", first_task_occurance_name)
                        print("new card name: ", card['name'])
                        first_task_occurance_name = card['name']
                        first_task_occurance_date = last_end_time
                    
                    print("Card name: ", card['name'], "Card estimated hours: ", card['estimated_hours'], "Card start time: ", last_end_time)
                    create_event(service, CALENDAR_TASKS_ID, card['name'], last_end_time, card['estimated_hours'])

                    end_time = last_end_time + timedelta(hours=card['estimated_hours'])


                    print("first_task_occurance_date: ", first_task_occurance_date, "end_time: ", end_time)
                    update_card_dates(card['id'], first_task_occurance_date, end_time)
                    
                    last_end_time += timedelta(hours=card['estimated_hours'])
                    
                cards = unallocated_cards
                
                print("--------------------")
        
        page_token = events_result.get('nextPageToken')
        if not page_token:
            break

        if (len(allocated_cards)== 0 and len(unallocated_cards) == 0):
            print("End of cards")
            break

main()
