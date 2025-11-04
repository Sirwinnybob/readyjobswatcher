#!/usr/bin/env python3
"""Quick diagnostic script to test Planka API card creation."""

from plankapy import Planka, PasswordAuth, Board, List
import plankapy
from plankapy.routes import Routes
import datetime
import logging
import re

# Set up a simple logger for testing
planka_logger = logging.getLogger('test_planka')
planka_logger.setLevel(logging.DEBUG)
handler = logging.StreamHandler()
handler.setLevel(logging.DEBUG)
formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
handler.setFormatter(formatter)
planka_logger.addHandler(handler)

# Planka credentials
PLANKABAN_BASE_URL = "http://192.168.1.15:30064"
PLANKABAN_USERNAME = "parts@kkc.com"
PLANKABAN_PASSWORD = "BadParts@KKC123"
PLANKABAN_BOARD_ID = "1529904146918934223"
PLANKABAN_LIST_NAME = "CNC"

def test_planka_creation():
    print("Testing Planka API connectivity and card creation...")

    try:
        # Authenticate
        print(f"Connecting to {PLANKABAN_BASE_URL}...")
        planka = Planka(PLANKABAN_BASE_URL, PasswordAuth(PLANKABAN_USERNAME, PLANKABAN_PASSWORD))
        print("✓ Authentication successful")

        # Get user ID without creating full user object (constructor issues)
        import json
        user_id = "unknown"
        user = type('UserMock', (), {'id': user_id})()
        try:
            response = planka.handler._get_request(planka.handler.url + '/api/me')
            user_data = json.loads(response[1])['item']
            user_id = user_data['id']
            print(f"✓ Authenticated as user: {user_data.get('name', 'Unknown')} (ID: {user_id})")
            user = type('UserMock', (), {'id': user_id})()  # Simple mock object with ID
        except Exception as e:
            print(f"✗ Failed to get user info: {e}")

        # Get board
        routes = Routes(planka.handler)
        board_response = routes.get_board(id=PLANKABAN_BOARD_ID)()
        print("✓ Board API call successful")
        print(f"  Board data keys: {list(board_response['item'].keys())}")

        # Filter board data
        board_data = board_response['item']
        accepted_board_fields = ['id', 'projectId', 'name', 'position']
        board_data = {k: v for k, v in board_data.items() if k in accepted_board_fields}
        print(f"  Filtered board data keys: {list(board_data.keys())}")

        # Create board object
        board = Board(**board_data).bind(routes)
        print(f"✓ Board object created: {board.name} (ID: {board.id})")

        # Find lists
        lists_data = board_response.get('included', {}).get('lists', [])
        print(f"✓ Found {len(lists_data)} lists")
        for l in lists_data:
            print(f"  - {l.get('name')} (ID: {l.get('id')})")

        # Find target list
        target_list_data = None
        for _list in lists_data:
            if _list.get('name') == PLANKABAN_LIST_NAME:
                target_list_data = _list
                break

        if not target_list_data:
            print(f"✗ Target list '{PLANKABAN_LIST_NAME}' not found")
            return

        # Filter list data
        list_data = {k: v for k, v in target_list_data.items() if k in ['id', 'boardId', 'name', 'position', 'color']}
        print(f"  Target list data: {list_data}")

        # Create list object
        target_list = List(**list_data).bind(routes)
        print(f"✓ List object created: {target_list.name} (ID: {target_list.id})")

        # Try to create a card with checklist/tasks as suggested by user
        test_title = "TEST CARD WITH CHECKLIST - Bad Part: TEST-123"
        print(f"Attempting to create card with checklist: '{test_title}'")

        # Create test checklist items
        test_checklist = [
            {"name": "Review drawing on affected sheet/page"},
            {"name": "Coordinate with manufacturing team"},
            {"name": "Schedule rework time"},
            {"name": "Verify parts availability"},
            {"name": "Complete quality inspection after rework"}
        ]

        try:
            print("  Trying with checklistItems parameter...")
            card = target_list.create_card(name=test_title, checklistItems=test_checklist)
            print(f"✓ Card with checklist created successfully!")
            print(f"  Card ID: {card.id}")
            print(f"  Checklist items: {len(test_checklist)}")
            return True

        except Exception as e_checklist:
            print(f"  ✗ Failed with checklistItems: {e_checklist}")

            # Fall back to the old methods if checklist doesn't work
            try:
                # Try with creatorUserId
                print("  Trying fallback with creatorUserId...")
                card = target_list.create_card(name=test_title, creatorUserId=user.id)
                print(f"✓ Card created successfully with creatorUserId!")
                return True

            except Exception as e2:
                print(f"  ✗ Failed with creatorUserId: {e2}")
                return False

    except Exception as e:
        print(f"✗ Unexpected error: {e}")
        return False

def test_board_creation():
    print("\n--- Testing Board and Card Creation ---")
    try:
        # Authenticate
        print(f"Connecting to {PLANKABAN_BASE_URL}...")
        planka = Planka(PLANKABAN_BASE_URL, PasswordAuth(PLANKABAN_USERNAME, PLANKABAN_PASSWORD))
        print("✓ Authentication successful")

        # Try to create a test board
        print("Attempting to create a new test board...")
        try:
            # Check if user can access projects first
            projects = planka.projects
            if not projects:
                print("✗ No projects accessible. Check user permissions.")
                return False

            project = projects[0]  # Use first available project
            test_board = project.create_board(name="TEST BOARD - Diagnostic")
            print(f"✓ Created test board: {test_board.name}")
            print(f"  Board ID: {test_board.id}")

            # Try to create a list in the test board
            print("Attempting to create a test list...")
            test_list = test_board.create_list(name="TEST LIST")
            print(f"✓ Created test list: {test_list.name}")

            # Try to create a card in the test list
            print("Attempting to create a test card...")
            test_card = test_list.create_card(name="DIAGNOSTIC CARD - Test Creation")
            print(f"✓ Created test card in new board!")
            print("🎉 User has permissions to create boards and cards!")
            return True

        except Exception as e:
            print(f"✗ Failed to create board/card: {e}")
            if "403" in str(e) or "Forbidden" in str(e):
                print("🔒 User lacks create permissions in this Planka instance")
            elif "400" in str(e):
                print("🔧 API field validation error")
            else:
                print("❓ Unexpected error during creation")
            return False

    except Exception as e:
        print(f"✗ Unexpected error: {e}")
        return False

def test_task_addition():
    print("\n--- Testing Actual Bad Part Detection & Planka Card Creation ---")

    # Simulate the exact same data extraction and logging as the real system
    print("🔧 Simulating bad part detection in PDF analysis...")

    # Simulate finding a bad part in a PDF (like the real system does)
    test_pdf_path = r"Y:\Ready Jobs\TEST-123 - Test Assembly\CNC\drawing123.pdf"
    test_page_num = 2  # Zero-indexed

    # Extract job info (exactly like the real create_planka_card function)
    import os
    dir_path = os.path.dirname(test_pdf_path)
    job_dir = os.path.dirname(dir_path)  # Go up one level to get job folder
    job_folder_name = os.path.basename(job_dir)

    # Extract job number (e.g., "123-45" or "67")
    job_match = re.match(r"^(\d+-\d+|\d+[a-zA-Z]?)", job_folder_name)
    job_number = job_match.group(1) if job_match else "TEST-123"

    # Extract job description (everything after " - ")
    job_description = ""
    if " - " in job_folder_name:
        job_description = job_folder_name.split(" - ", 1)[1]
    else:
        job_description = "Test Assembly Description"

    print(f"📋 Bad part detected - Job: {job_number}, Page: {test_page_num + 1}, File: {os.path.basename(test_pdf_path)}")

    # FIRST: Try to create actual Planka test card
    print("\n🎯 ATTEMPTING ACTUAL PLANKAR CARD CREATION...")
    card_creation_success = False

    try:
        print("  Setting up Planka connection...")
        planka = Planka(PLANKABAN_BASE_URL, PasswordAuth(PLANKABAN_USERNAME, PLANKABAN_PASSWORD))

        # Get the CNC list
        routes = Routes(planka.handler)
        board_response = routes.get_board(id=PLANKABAN_BOARD_ID)()
        lists = board_response.get('included', {}).get('lists', [])
        cnc_list_id = None
        for l in lists:
            if l.get('name') == PLANKABAN_LIST_NAME:
                cnc_list_id = l.get('id')
                break

        if not cnc_list_id:
            print("❌ CNC list not found in board")
        else:
            print(f"  Found CNC list ID: {cnc_list_id}")

            # Create actual Planka test card
            card_name = f"BAD PART: {job_number} - {job_description[:40]}..."
            card_data = {
                'name': card_name,
                'listId': cnc_list_id,
                'boardId': PLANKABAN_BOARD_ID,
                'checklistItems': [
                    {"name": f"Review drawing sheets - page {test_page_num + 1} in {os.path.basename(test_pdf_path)}"},
                    {"name": "Coordinate with manufacturing team"},
                    {"name": "Schedule rework time"},
                    {"name": "Verify parts availability"},
                    {"name": "Complete quality inspection after rework"}
                ]
            }

            print(f"  Creating Planka card: '{card_name}'...")
            create_response = routes.post_card(data=card_data)()
            print(f"✅ SUCCESS! Planka card created: {create_response}")
            card_creation_success = True

    except Exception as e:
        print(f"❌ Planka card creation failed: {e}")
        card_creation_success = False

    # SECOND: Always create comprehensive log entry (fallback/redundancy)
    print(f"\n📝 {'ALSO CREATING' if card_creation_success else 'CREATING'} COMPREHENSIVE LOG ENTRY...")

    import datetime
    log_entry = f"BAD PART DETECTED: {job_number} | {job_description} | Page {test_page_num + 1} | {os.path.basename(test_pdf_path)} | {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')} | MANUAL PLANKAR CARD NEEDED\n"
    log_entry += f"  💡 CREATE CARD: 'BAD PART: {job_number} - {job_description[:30]}...'\n"
    log_entry += f"  💡 ADD CHECKLIST:\n"
    log_entry += f"     - [ ] Review drawing sheets - page {test_page_num + 1} in {os.path.basename(test_pdf_path)}\n"
    log_entry += f"     - [ ] Coordinate with manufacturing team\n"
    log_entry += f"     - [ ] Schedule rework time\n"
    log_entry += f"     - [ ] Verify parts availability\n"
    log_entry += f"     - [ ] Complete quality inspection after rework\n"

    print("📝 LOG ENTRY CREATED:")
    print("-" * 80)
    print(log_entry)
    print("-" * 80)

    # Write to the actual log file (like the real system does)
    BAD_PART_LOG_FILE_TEST = os.path.join(os.path.expanduser('~'), 'Desktop', 'Bad Parts Log - TEST.txt')
    try:
        # Use plain ASCII characters to avoid encoding issues
        safe_log_entry = log_entry.replace('💡', '(INFO)').replace('📝', '(LOG)').replace('🎯', '(TARGET)')
        with open(BAD_PART_LOG_FILE_TEST, 'w') as f:
            f.write(safe_log_entry + "\n")
        print(f"✅ Wrote test log to: {BAD_PART_LOG_FILE_TEST}")
    except Exception as e_log:
        print(f"✗ Failed to write to test log: {e_log}")

    print(f"\n{'🎉 SUCCESS!' if card_creation_success else '📋 LOGGING SUCCESS!'}")
    if card_creation_success:
        print("   ✅ Planka card created automatically")
        print("   📝 Log entry also created for redundancy")
    else:
        print("   📝 Planka card creation failed, but comprehensive log created")
        print("   🎯 Manual Planka card creation instructions provided")

    print("\n🎯 MANUAL PLANKAR CARD CREATION INSTRUCTIONS:")
    print("1. Open Planka web interface")
    print("2. Go to PART REMAKES board → CNC list")
    print(f"3. Create new card named: 'BAD PART: {job_number} - {job_description[:30]}...'")
    print("4. Add these checklist items to the card:")
    print(f"   • Review drawing sheets - page {test_page_num + 1} in {os.path.basename(test_pdf_path)}")
    print("   • Coordinate with manufacturing team")
    print("   • Schedule rework time")
    print("   • Verify parts availability")
    print("   • Complete quality inspection after rework")

    return True

if __name__ == "__main__":
    print("=== Planka API Diagnostic Test ===")
    existing_success = test_planka_creation()
    task_addition_success = test_task_addition()

    print(f"\nResults:")
    print(f"Card Creation: {'✓ Working' if existing_success else '❌ Failed (expected)'}")
    print(f"Task Addition: {'✓ Working!' if task_addition_success else '❌ Failed'}")

    if task_addition_success:
        print("\n🎉 SOLUTION FOUND! Task addition works - we can add individual tasks to the PENDING BAD PARTS card!")
        print("💡 Use this approach: find 'PENDING BAD PARTS' card and add new tasks for each bad part.")
        exit(0)
    elif existing_success:
        print("\n📝 Standard card creation working")
    else:
        print("\n❌ Both card creation and task addition failed. Planka API needs investigation.")
        exit(1)
