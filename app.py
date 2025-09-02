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
UPDATE_INTERVAL_HOURS = 12

# --- Page Config ---
st.set_page_config(layout="wide")
st.title('FPL Tactical Analysis Dashboard ⚽')

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

def get_player_gameweek_history(player_id):
    """Fetches the gameweek history for a specific player."""
    url = f"https://fantasy.premierleague.com/api/element-summary/{player_id}/"
    try:
        response = requests.get(url, verify=False)
        response.raise_for_status()
        time.sleep(0.05)
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
            cost REAL, total_points INTEGER, display_name TEXT,
            points_per_million REAL, ownership_percent REAL
        )""")
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS gameweek_history (
            player_id INTEGER, gameweek INTEGER, total_points INTEGER, minutes INTEGER,
            FOREIGN KEY (player_id) REFERENCES players (id), PRIMARY KEY (player_id, gameweek)
        )""")
        conn.commit()

def update_database():
    """Fetches fresh data and updates the database. Shows progress in Streamlit."""
    with sqlite3.connect(DB_NAME) as conn:
        cursor = conn.cursor()
        cursor.execute("DROP TABLE IF EXISTS players")
        cursor.execute("DROP TABLE IF EXISTS gameweek_history")
    
    create_database_tables()
    
    status_message = st.status("Fetching fresh data from FPL API...", expanded=True)
    fpl_data = get_fpl_data()
    if not fpl_data:
        status_message.error("Could not fetch FPL data. Aborting update.")
        return

    players_df = pd.DataFrame(fpl_data['elements'])
    teams_df = pd.DataFrame(fpl_data['teams'])
    positions_df = pd.DataFrame(fpl_data['element_types'])
    team_map = teams_df.set_index('id')['name']
    position_map = positions_df.set_index('id')['singular_name_short']
    players_df['team_name'] = players_df['team'].map(team_map)
    players_df['position'] = players_df['element_type'].map(position_map)
    players_df['cost'] = players_df['now_cost'] / 10.0
    players_df['display_name'] = players_df['web_name'] + " (" + players_df['team_name'] + ")"
    players_df['ownership_percent'] = pd.to_numeric(players_df['selected_by_percent'])
    players_df['points_per_million'] = (players_df['total_points'] / players_df['cost']).fillna(0)

    players_to_db = players_df[['id', 'web_name', 'team_name', 'position', 'cost', 'total_points', 
                                'display_name', 'points_per_million', 'ownership_percent']]
    
    with sqlite3.connect(DB_NAME) as conn:
        players_to_db.to_sql('players', conn, if_exists='replace', index=False)
        status_message.write(f"Updated {len(players_to_db)} players.")
        
        status_message.write("Updating gameweek history...")
        progress_bar = st.progress(0)
        cursor = conn.cursor()
        all_player_ids = players_df['id'].tolist()
        total_players = len(all_player_ids)

        for i, player_id in enumerate(all_player_ids):
            history_data = get_player_gameweek_history(player_id)
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
    """Checks the database file and schema, triggering an update if needed."""
    db_exists = os.path.exists(DB_NAME)
    if not db_exists:
        st.info("Database not found. Creating and populating a new one.")
        create_database_tables()
        update_database()
        return

    is_schema_correct = False
    try:
        with sqlite3.connect(DB_NAME) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT points_per_million FROM players LIMIT 1")
            is_schema_correct = True
    except sqlite3.OperationalError:
        st.warning("Database schema is outdated. Triggering a full update.")
        update_database()

    if is_schema_correct:
        last_modified_time = datetime.fromtimestamp(os.path.getmtime(DB_NAME))
        if datetime.now() - last_modified_time > timedelta(hours=UPDATE_INTERVAL_HOURS):
            st.info("Database is older than 12 hours. Fetching fresh data...")
            update_database()

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

    st.sidebar.header('Filters')
    selected_position = st.sidebar.selectbox('Select Player Position', options=['All'] + sorted(players_df['position'].unique()))
    all_teams = ['All Teams'] + sorted(players_df['team_name'].unique())
    selected_team = st.sidebar.selectbox('Select Team', options=all_teams)

    st.sidebar.header('Sort Players By')
    sort_by = st.sidebar.selectbox('Metric', ['Total Points', 'Points per Million (£)', 'Ownership (%)'])
    sort_order = st.sidebar.radio('Order', ['Descending', 'Ascending'], index=0)

    filtered_players = players_df.copy()
    if selected_position != 'All':
        filtered_players = filtered_players[filtered_players['position'] == selected_position]
    if selected_team != 'All Teams':
        filtered_players = filtered_players[filtered_players['team_name'] == selected_team]
    
    sort_map = {
        'Total Points': 'total_points',
        'Points per Million (£)': 'points_per_million',
        'Ownership (%)': 'ownership_percent'
    }
    is_ascending = sort_order == 'Ascending'
    filtered_players = filtered_players.sort_values(by=sort_map[sort_by], ascending=is_ascending)

    st.header('Player Data Explorer')
    display_cols = ['display_name', 'position', 'cost', 'total_points', 'points_per_million', 'ownership_percent']
    st.dataframe(filtered_players[display_cols].rename(columns={
        'display_name': 'Player', 'position': 'Pos', 'cost': 'Cost (£m)',
        'total_points': 'Points', 'points_per_million': 'Points/£m (ROI)',
        'ownership_percent': 'Ownership (%)'
    }).set_index('Player'), use_container_width=True)

    st.header('Compare Player Point Progression')
    player_options = filtered_players['display_name'].tolist()
    if player_options:
        selected_players = st.multiselect('Select players to compare:', options=player_options)
        if selected_players:
            player_ids_to_chart = filtered_players[filtered_players['display_name'].isin(selected_players)]['id'].tolist()
            chart_df_filtered = history_df[history_df['player_id'].isin(player_ids_to_chart)].copy()
            player_map = players_df.set_index('id')['display_name']
            chart_df_filtered['Player'] = chart_df_filtered['player_id'].map(player_map)
            
            # --- THIS IS THE FIX ---
            # Enforce numeric type for the points column before calculating cumulative sum
            chart_df_filtered['total_points'] = pd.to_numeric(chart_df_filtered['total_points'], errors='coerce').fillna(0)
            
            chart_df_filtered['Cumulative Points'] = chart_df_filtered.groupby('Player')['total_points'].cumsum()
            
            if not chart_df_filtered.empty:
                fig = px.line(chart_df_filtered, x='gameweek', y='Cumulative Points', color='Player',
                              title=f'Cumulative Points Progression', markers=True)
                st.plotly_chart(fig, use_container_width=True)

except Exception as e:
    st.error(f"An error occurred while rendering the dashboard: {e}")

