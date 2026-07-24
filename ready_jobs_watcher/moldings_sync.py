"""
Molding Library Synchronization Module.

This module provides the logic to query Cabinet Vision molding profiles,
parse/modify their XML, output them to the target directory inside ROOT_DIR,
and clean up obsolete files.
"""

import os
import logging
import xml.etree.ElementTree as ET
import pyodbc

from .atomic_write import atomic_write_bytes

logger = logging.getLogger('main')


def sync_moldings_library(config) -> bool:
    """
    Synchronize Cabinet Vision molding profiles to the Ready Jobs metadata folder.

    Args:
        config: The configuration object containing db credentials and ROOT_DIR.

    Returns:
        bool: True on success, False on failure.
    """
    try:
        target_dir = os.path.join(config.ROOT_DIR, '.metadata', 'moldings')
        if not os.path.exists(target_dir):
            os.makedirs(target_dir, exist_ok=True)
            logger.info(f"Created moldings metadata directory: {target_dir}")
    except Exception as e:
        logger.error(f"Failed to create target moldings directory: {e}")
        return False

    # Connect to the Cabinet Vision database using pyodbc
    conn_str = (
        f"Driver={{ODBC Driver 17 for SQL Server}};"
        f"Server={config.db_server};"
        f"Database={config.db_name};"
        f"UID={config.db_user};"
        f"PWD={config.db_password};"
        f"TrustServerCertificate=yes;"
    )
    
    rows = []
    conn = None
    try:
        logger.debug(f"Connecting to Cabinet Vision database: Server={config.db_server}, Database={config.db_name}")
        # Add connection timeout of 5 seconds
        conn = pyodbc.connect(conn_str, timeout=5)
        # Set conn.timeout explicitly to enforce query execution timeout
        conn.timeout = 5
        
        with conn.cursor() as cursor:
            query = """
            SELECT p.ID, p.Name, p.ProfileTypeID, s.Shape
            FROM Profile p WITH (NOLOCK)
            JOIN Shape s WITH (NOLOCK) ON p.ShapeID = s.ID
            WHERE p.ProfileTypeID IN (1, 4, 5)
            """
            cursor.execute(query)
            rows = cursor.fetchall()
    except Exception as e:
        logger.error(f"Failed to connect or query Cabinet Vision database: {e}")
        return False
    finally:
        if conn:
            try:
                conn.close()
                logger.debug("Database connection closed successfully.")
            except Exception as e:
                logger.warning(f"Error closing database connection: {e}")

    PROFILE_TYPE_MAP = {
        1: "Crown",
        4: "Scribe",
        5: "Base",
    }

    db_profile_ids = set()
    updated_count = 0
    skipped_count = 0
    parse_error_count = 0
    unchanged_count = 0

    for row in rows:
        try:
            profile_id = row[0]
            profile_name = row[1]
            profile_type_id = row[2]
            shape_xml = row[3]

            if profile_id is None:
                continue

            db_profile_ids.add(str(profile_id))

            subfolder_name = PROFILE_TYPE_MAP.get(profile_type_id)
            if not subfolder_name:
                logger.debug(f"Skipping profile {profile_id} ({profile_name}): Unknown profile type ID {profile_type_id}.")
                skipped_count += 1
                continue

            if not shape_xml:
                logger.debug(f"Skipping profile {profile_id} ({profile_name}): Shape XML is null or empty.")
                skipped_count += 1
                continue

            # Parse the Shape XML
            try:
                if isinstance(shape_xml, bytes):
                    root = ET.fromstring(shape_xml)
                else:
                    root = ET.fromstring(shape_xml.strip())
                
                # Set the name attribute of the root element to the profile's Name from the database
                root.set('name', profile_name or "")
                
                # Convert the modified XML back to bytes
                new_bytes = ET.tostring(root, encoding='utf-8', xml_declaration=True)
            except Exception as e:
                logger.error(f"Failed to parse/modify XML for molding profile ID {profile_id} ({profile_name}): {e}")
                parse_error_count += 1
                continue

            subfolder_dir = os.path.join(target_dir, subfolder_name)
            os.makedirs(subfolder_dir, exist_ok=True)
            filepath = os.path.join(subfolder_dir, f"{profile_id}.xml")
            write_needed = True

            # Optimize: skip writing if the file already exists and has identical content
            if os.path.exists(filepath):
                try:
                    with open(filepath, 'rb') as f:
                        existing_bytes = f.read()
                    if existing_bytes == new_bytes:
                        write_needed = False
                except Exception as e:
                    logger.warning(f"Error reading existing profile file {filepath} for comparison: {e}")

            if write_needed:
                try:
                    atomic_write_bytes(filepath, new_bytes)
                    updated_count += 1
                    logger.debug(f"Successfully sync'd molding profile ID {profile_id} ({profile_name}) in {subfolder_name}")
                except Exception as e:
                    logger.error(f"Failed to write molding profile file {filepath}: {e}")
                    # Continue to next profiles on file write error
                    continue
            else:
                unchanged_count += 1

        except Exception as e:
            logger.error(f"Unexpected error processing profile row: {e}")
            continue

    # Clean up obsolete/legacy files
    deleted_count = 0
    try:
        # 1. Clean up legacy flat [ID].xml files in the root target_dir
        if os.path.exists(target_dir):
            for filename in os.listdir(target_dir):
                if filename.endswith('.xml'):
                    name_without_ext = filename[:-4]
                    if name_without_ext.isdigit():
                        filepath = os.path.join(target_dir, filename)
                        try:
                            os.remove(filepath)
                            deleted_count += 1
                            logger.info(f"Deleted legacy flat molding profile file: {filepath}")
                        except Exception as e:
                            logger.error(f"Failed to delete legacy flat profile file {filepath}: {e}")

        # 2. Clean up obsolete files inside the subfolders (Crown, Scribe, Base)
        subfolders_to_clean = ["Crown", "Scribe", "Base"]
        for subfolder_name in subfolders_to_clean:
            subfolder_path = os.path.join(target_dir, subfolder_name)
            if os.path.exists(subfolder_path):
                for filename in os.listdir(subfolder_path):
                    if filename.endswith('.xml'):
                        name_without_ext = filename[:-4]
                        if name_without_ext.isdigit():
                            if name_without_ext not in db_profile_ids:
                                filepath = os.path.join(subfolder_path, filename)
                                try:
                                    os.remove(filepath)
                                    deleted_count += 1
                                    logger.info(f"Deleted obsolete molding profile file: {filepath}")
                                except Exception as e:
                                    logger.error(f"Failed to delete obsolete profile file {filepath}: {e}")
    except Exception as e:
        logger.error(f"Failed to clean up obsolete/legacy molding files: {e}")
        return False

    logger.info(
        f"Molding synchronization completed. "
        f"Updated/Created: {updated_count}, Unchanged: {unchanged_count}, "
        f"Skipped (empty): {skipped_count}, Parse errors: {parse_error_count}, "
        f"Deleted obsolete: {deleted_count}."
    )
    return True
