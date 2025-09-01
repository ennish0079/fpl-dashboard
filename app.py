#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Mon Sep  1 21:26:46 2025

@author: hany
"""

import streamlit as st
import pandas as pd
import plotly.express as px
import sqlite3
import requests
import warnings
import time
import os
from datetime import datetime, timedelta

# Suppress InsecureRequestWarning
from requests.packages.urllib3.exceptions import InsecureRequestWarning
warnings.simplefilter('ignore', InsecureRequestWarning)

DB_NAME = 'fpl.db'
UPDATE_INTERVAL_HOURS = 12  # How old the database can be before an update is triggered

# --- Page Config ---
st.set_page_config(layout="wide")
st.title('FPL Player Performance Dashboard ⚽')

# --- Data Fetching and Database Functions ---

def get_fpl_data():
    """Fetches the main FPL bootstrap data from the official API."""
    url = "https://fantasy.premierleague.com/api/bootstrap-static/"
    try:
        response = requests.get(url, verify=False)
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as e:
        st.error(f"Error fetching main FPL data: {e}")
        return None

def get_player_gameweek_history(player_id, progress_bar):
    """Fetches the gameweek history for a specific player."""
    url = f"https://fantasy.premierleague.com/api/element-summary/{player_id}/"
    try:
        response = requests.get(url, verify=False)
        response.raise_for_status()
        time.sleep(0.05) # Be polite to the API
        return response.json()
    except requests.exceptions.RequestException:
        return None

def create_database_tables():
    """Creates the SQLite database tables if they don't exist."""
    with sqlite3.connect(DB_NAME) as conn:
        cursor = conn.cursor()
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS players (
            id INTEGER PRIMARY KEY, web_name TEXT, team_name TEXT, position TEXT,
            cost REAL, total_points INTEGER, display_name TEXT
        )""")
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS gameweek_history (
            player_id INTEGER, gameweek INTEGER, total_points INTEGER, minutes INTEGER,
            FOREIGN KEY (player_id) REFERENCES players (id), PRIMARY KEY (player_id, gameweek)
        )""")
        conn.commit()

def update_database():
    """Fetches fresh data and updates the database. Shows progress in Streamlit."""
    status_message = st.status("Fetching fresh data from FPL API...", expanded=True)
    
    fpl_data = get_fpl_data()
    if not fpl_data:
        status_message.error("Could not fetch FPL data. Aborting update.")
        return

    # Process players
    players_df = pd.DataFrame(fpl_data['elements'])
    teams_df = pd.DataFrame(fpl_data['teams'])
    positions_df = pd.DataFrame(fpl_data['element_types'])
    team_map = teams_df.set_index('id')['name']
    position_map = positions_df.set_index('id')['singular_name_short']
    players_df['team_name'] = players_df['team'].map(team_map)
    players_df['position'] = players_df['element_type'].map(position_map)
    players_df['cost'] = players_df['now_cost'] / 10.0
    players_df['display_name'] = players_df['web_name'] + " (" + players_df['team_name'] + ")"
    players_to_db = players_df[['id', 'web_name', 'team_name', 'position', 'cost', 'total_points', 'display_name']]
    
    with sqlite3.connect(DB_NAME) as conn:
        players_to_db.to_sql('players', conn, if_exists='replace', index=False)
        status_message.write(f"Updated {len(players_to_db)} players in the database.")

        # Process gameweek history
        status_message.write("Updating gameweek history for all players... (This may take a minute)")
        progress_bar = st.progress(0)
        cursor = conn.cursor()
        all_player_ids = players_df['id'].tolist()
        total_players = len(all_player_ids)

        for i, player_id in enumerate(all_player_ids):
            history_data = get_player_gameweek_history(player_id, progress_bar)
            if history_data and 'history' in history_data:
                for gw in history_data['history']:
                    cursor.execute("INSERT OR REPLACE INTO gameweek_history VALUES (?, ?, ?, ?)",
                                   (player_id, gw['round'], gw['total_points'], gw['minutes']))
            progress_bar.progress((i + 1) / total_players, text=f"Processing player {i+1}/{total_players}")
        
        conn.commit()
    
    status_message.success("Database update complete!")
    time.sleep(2)
    st.rerun()

def check_and_update_db():
    """Checks the database file's age and triggers an update if it's too old."""
    db_exists = os.path.exists(DB_NAME)
    if not db_exists:
        st.info(f"Database '{DB_NAME}' not found. Creating and populating a new one.")
        create_database_tables()
        update_database()
    else:
        last_modified_time = datetime.fromtimestamp(os.path.getmtime(DB_NAME))
        if datetime.now() - last_modified_time > timedelta(hours=UPDATE_INTERVAL_HOURS):
            st.info("Database is older than 12 hours. Fetching fresh data...")
            update_database()

# --- Data Loading Function from SQLite ---
@st.cache_data(ttl=3600)
def load_data_from_db():
    """Loads all data from the SQLite database."""
    with sqlite3.connect(DB_NAME) as conn:
        players_df = pd.read_sql_query("SELECT * FROM players", conn)
        history_df = pd.read_sql_query("SELECT * FROM gameweek_history", conn)
    return players_df, history_df

# --- Main App Logic ---
check_and_update_db()

try:
    players_df, history_df = load_data_from_db()

    # --- Sidebar Filters ---
    st.sidebar.header('Filters')
    selected_position = st.sidebar.selectbox('Select Player Position', options=sorted(players_df['position'].unique()))
    all_teams = ['All Teams'] + sorted(players_df['team_name'].unique())
    selected_team = st.sidebar.selectbox('Select Team', options=all_teams)

    # Apply filters
    filtered_players = players_df[players_df['position'] == selected_position]
    if selected_team != 'All Teams':
        filtered_players = filtered_players[filtered_players['team_name'] == selected_team]
    filtered_players = filtered_players.sort_values(by='total_points', ascending=False)

    # --- Player Selection ---
    st.header('Compare Player Point Progression')
    selected_players = st.multiselect('Select players to compare:', options=filtered_players['display_name'].tolist())

    # --- Chart Generation ---
    if selected_players:
        player_ids_to_chart = filtered_players[filtered_players['display_name'].isin(selected_players)]['id'].tolist()
        chart_df_filtered = history_df[history_df['player_id'].isin(player_ids_to_chart)].copy()
        player_map = players_df.set_index('id')['display_name']
        chart_df_filtered['Player'] = chart_df_filtered['player_id'].map(player_map)
        
        chart_df_filtered = chart_df_filtered.sort_values(by=['Player', 'gameweek'])
        chart_df_filtered['Cumulative Points'] = chart_df_filtered.groupby('Player')['total_points'].cumsum()
        
        if not chart_df_filtered.empty:
            fig = px.line(chart_df_filtered, x='gameweek', y='Cumulative Points', color='Player',
                          title=f'Cumulative Points Progression for {selected_position}s', markers=True,
                          labels={'Cumulative Points': 'Total Points', 'gameweek': 'Gameweek'})
            st.plotly_chart(fig, use_container_width=True)
        else:
            st.warning("No history found for selected players.")
    else:
        st.info('Select one or more players to see their progress.')

except (sqlite3.OperationalError, pd.io.sql.DatabaseError):
    st.error(f"Error reading from the database '{DB_NAME}'. It might be being updated. Please wait a moment and the page will refresh.")
    time.sleep(5)
    st.rerun()
