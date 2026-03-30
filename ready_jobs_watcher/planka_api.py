import os
import re
import logging
import datetime
from typing import Optional, Dict, Any
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError
from plankapy import Planka, PasswordAuth, interfaces
from plankapy.routes import Routes

from .notifications import send_notification
from .bad_parts_checker import BAD_PART_LOG_FILE
from .config import Config
from . import planka_credentials

planka_logger = logging.getLogger('planka')

# Planka integration - credentials loaded via planka_credentials.initialize_planka_credentials()
PLANKABAN_TIMEOUT = int(os.getenv("PLANKA_TIMEOUT", "10"))

PLANKABAN_BOARD_IDENTIFIER = None
PLANKABAN_BOARD_ID = None
PLANKABAN_LIST_NAME = None

# Custom compatible classes
class CompatibleProject(interfaces.Project):
    def __init__(self, *args, **kwargs):
        known_fields = {'id', 'name', 'background', 'backgroundImage', 'position'}
        filtered_kwargs = {k: v for k, v in kwargs.items() if k in known_fields}
        super().__init__(*args, **filtered_kwargs)

    @property
    def boards(self) -> interfaces.QueryableList[interfaces.Board]:
        board_objects = []
        for board in self._included['boards']:
            known_fields = {'id', 'name', 'position', 'projectId'}
            board_filtered = {k: v for k, v in board.items() if k in known_fields}
            board_objects.append(CompatibleBoard(**board_filtered).bind(self.routes))
        return interfaces.QueryableList(board_objects)

class CompatibleBoard(interfaces.Board):
    def __init__(self, *args, **kwargs):
        known_fields = {'id', 'name', 'position', 'projectId'}
        filtered_kwargs = {k: v for k, v in kwargs.items() if k in known_fields}
        super().__init__(*args, **filtered_kwargs)

    @property
    def lists(self) -> interfaces.QueryableList[interfaces.List]:
        list_objects = []
        for _list in self._included['lists']:
            known_fields = {'id', 'name', 'position', 'boardId'}
            list_filtered = {k: v for k, v in _list.items() if k in known_fields}
            list_objects.append(CompatibleList(**list_filtered).bind(self.routes))
        return interfaces.QueryableList(list_objects)

class CompatibleList(interfaces.List):
    def create_card(self, *args, **kwargs):
        from plankapy.interfaces import Card
        overload = parse_overload(args, kwargs, model='card', options=('name', 'position', 'description', 'dueDate', 'isDueDateCompleted', 'stopwatch', 'creatorUserId', 'coverAttachmentId', 'isSubscribed'), required=('name',))
        overload['boardId'] = self.boardId
        overload['listId'] = self.id
        overload['position'] = overload.get('position', 0)
        overload['type'] = 'project'
        route = self.routes.post_card(id=self.id)
        known_card_fields = {'id', 'name', 'position', 'description', 'dueDate', 'isDueDateCompleted', 'stopwatch', 'creatorUserId', 'listId', 'boardId', 'isSubscribed', 'coverAttachmentId'}
        card_response = route(**overload)['item']
        card_filtered = {k: v for k, v in card_response.items() if k in known_card_fields}
        return Card(**card_filtered).bind(self.routes)

class CompatiblePlanka(Planka):
    @property
    def projects(self) -> interfaces.QueryableList[interfaces.Project]:
        route = self.routes.get_project_index()
        project_objects = []
        for project in route()['items']:
            known_fields = {'id', 'name', 'background', 'backgroundImage', 'position'}
            project_filtered = {k: v for k, v in project.items() if k in known_fields}
            project_objects.append(CompatibleProject(**project_filtered).bind(self.routes))
        return interfaces.QueryableList(project_objects)

def internal_parse_overload(args: tuple, kwargs: dict, model: str, options: tuple, required: tuple = (), noarg: Optional[Dict[str, Any]] = None) -> dict:
    if isinstance(options, str):
        options = (options,)
    if isinstance(required, str):
        required = (required,)
    if args and isinstance(args[0], interfaces.Model) or model in kwargs:
        return {**args[0]} if args else {**kwargs[model]}
    elif args:
        coded_args = dict(zip(options, args))
        kwargs.update(coded_args)
    elif noarg and not kwargs:
        return {**noarg}
    if not all([arg in kwargs for arg in required]):
        raise ValueError(f'Required: {required}')
    return kwargs

try:
    from plankapy.interfaces import parse_overload
except ImportError:
    parse_overload = internal_parse_overload


def run_with_timeout(func, timeout_seconds: int = None, description: str = "operation"):
    """
    Run a function with a timeout. Raises TimeoutError if the operation takes too long.

    Args:
        func: Callable to execute
        timeout_seconds: Maximum time to wait (defaults to PLANKABAN_TIMEOUT)
        description: Description for logging

    Returns:
        The result of func()
    """
    if timeout_seconds is None:
        timeout_seconds = PLANKABAN_TIMEOUT

    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(func)
        try:
            return future.result(timeout=timeout_seconds)
        except FuturesTimeoutError:
            planka_logger.error(f"❌ {description} timed out after {timeout_seconds} seconds")
            raise TimeoutError(f"{description} timed out after {timeout_seconds} seconds")

def resolve_board_id(planka: Planka) -> Optional[str]:
    global PLANKABAN_BOARD_ID
    if PLANKABAN_BOARD_ID and PLANKABAN_BOARD_ID != "unknown":
        return PLANKABAN_BOARD_ID
    try:
        routes = Routes(planka.handler)
        if PLANKABAN_BOARD_IDENTIFIER and PLANKABAN_BOARD_IDENTIFIER.replace('-', '').replace('_', '').isalnum():
            try:
                planka_logger.debug(f"Attempting board lookup by ID: {PLANKABAN_BOARD_IDENTIFIER}")
                routes.get_board(id=PLANKABAN_BOARD_IDENTIFIER)()
                PLANKABAN_BOARD_ID = PLANKABAN_BOARD_IDENTIFIER
                planka_logger.debug(f"Board resolved by ID: {PLANKABAN_BOARD_ID}")
                return PLANKABAN_BOARD_ID
            except Exception as e:
                planka_logger.debug(f"Direct board ID lookup failed: {e}. Trying name lookup.")
        if PLANKABAN_BOARD_IDENTIFIER:
            try:
                planka_logger.debug(f"Attempting board lookup by name: {PLANKABAN_BOARD_IDENTIFIER}")
                projects = planka.projects
                for project in projects:
                    project_data = {k: v for k, v in project.__dict__.items() if k in ['id']}
                    proj = type('Project', (), project_data)()
                    try:
                        boards = proj.boards
                        for board in boards:
                            if board.name == PLANKABAN_BOARD_IDENTIFIER:
                                PLANKABAN_BOARD_ID = board.id
                                planka_logger.info(f"Board resolved by name '{PLANKABAN_BOARD_IDENTIFIER}' -> ID: {PLANKABAN_BOARD_ID}")
                                return PLANKABAN_BOARD_ID
                    except Exception as e:
                        planka_logger.debug(f"Error accessing boards for project {proj.id}: {e}")
            except Exception as e:
                planka_logger.error(f"Board name lookup failed: {e}")
        PLANKABAN_BOARD_ID = "unknown"
        return None
    except Exception as e:
        planka_logger.error(f"Board resolution failed completely: {e}")
        PLANKABAN_BOARD_ID = "unknown"
        return None

def create_planka_card(pdf_path: str, page_num: int, config: Config) -> None:
    # Get credentials from the credential helper
    PLANKABAN_BASE_URL, PLANKABAN_USERNAME, PLANKABAN_PASSWORD = planka_credentials.get_planka_credentials()

    # Skip if Planka credentials are not configured
    if not PLANKABAN_USERNAME or not PLANKABAN_PASSWORD:
        planka_logger.debug("Planka credentials not configured, skipping card creation")
        return

    try:
        planka_logger.info("🔧 Starting Planka card creation for bad part detection")

        dir_path = os.path.dirname(pdf_path)
        job_dir = os.path.dirname(dir_path)
        job_folder_name = os.path.basename(job_dir)

        job_match = re.match(r"^(\d+-\d+|\d+[a-zA-Z]?)", job_folder_name)
        job_number = job_match.group(1) if job_match else "Unknown Job"

        job_description = ""
        if " - " in job_folder_name:
            job_description = job_folder_name.split(" - ", 1)[1]

        planka_logger.info(f"📋 Bad part detected - Job: {job_number}, Page: {page_num + 1}, File: {os.path.basename(pdf_path)}")

        try:
            # Connect to Planka with timeout
            def connect_planka():
                return CompatiblePlanka(PLANKABAN_BASE_URL, PasswordAuth(PLANKABAN_USERNAME, PLANKABAN_PASSWORD))

            planka_logger.debug(f"Connecting to Planka (timeout: {PLANKABAN_TIMEOUT}s)...")
            with ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(connect_planka)
                try:
                    planka = future.result(timeout=PLANKABAN_TIMEOUT)
                except FuturesTimeoutError:
                    planka_logger.error(f"❌ Planka connection timed out after {PLANKABAN_TIMEOUT} seconds")
                    raise TimeoutError(f"Planka connection timed out after {PLANKABAN_TIMEOUT} seconds")

            global PLANKABAN_BOARD_IDENTIFIER, PLANKABAN_LIST_NAME
            PLANKABAN_BOARD_IDENTIFIER = config.planka_board_identifier
            PLANKABAN_LIST_NAME = config.planka_list_name

            # Get projects with timeout
            projects = run_with_timeout(lambda: planka.projects, description="Get projects")
            if not projects:
                planka_logger.error("❌ No projects available in Planka")
                return

            project = projects[0]
            planka_logger.info(f"✅ Using project: {project.name}")

            # Get boards with timeout
            boards = run_with_timeout(lambda: project.boards, description="Get boards")
            planka_logger.debug(f"✅ Found {len(boards)} boards in project")

            target_board = next((b for b in boards if b.id == PLANKABAN_BOARD_IDENTIFIER), None)
            if not target_board:
                planka_logger.error(f"❌ Target board {PLANKABAN_BOARD_IDENTIFIER} not found in project")
                return

            planka_logger.info(f"✅ Using board: {target_board.name} (ID: {target_board.id})")

            # Get lists with timeout
            lists = run_with_timeout(lambda: target_board.lists, description="Get lists")
            planka_logger.debug(f"✅ Found {len(lists)} lists in board")

            cnc_list = next((l for l in lists if l.name == PLANKABAN_LIST_NAME), None)
            if not cnc_list:
                planka_logger.error(f"❌ '{PLANKABAN_LIST_NAME}' list not found in board")
                return

            planka_logger.info(f"✅ Using list: {cnc_list.name} (ID: {cnc_list.id})")

            desc_limit = 30
            display_description = job_description[:desc_limit] + "..." if len(job_description) > desc_limit else job_description
            card_name = f"BAD PART: {job_number} - {display_description}"
            planka_logger.info(f"📝 Creating card: '{card_name}'")

            # Create card with timeout
            new_card = run_with_timeout(lambda: cnc_list.create_card(name=card_name), description="Create card")
            planka_logger.info(f"✅ Card created successfully: '{new_card.name}' (ID: {new_card.id})")

            tasks_data = [
                f"Review drawing sheets - page {page_num + 1} in {os.path.basename(pdf_path)}",
                "Coordinate with manufacturing team",
                "Schedule rework time",
                "Verify parts availability",
                "Complete quality inspection after rework"
            ]

            planka_logger.info("🛠️ Adding checklist tasks to card...")
            for i, task_name in enumerate(tasks_data):
                try:
                    task = new_card.add_task(name=task_name, position=i, isCompleted=False)
                    planka_logger.info(f"   ✅ Task {i+1} added: '{task.name}' (ID: {task.id})")
                except Exception as e:
                    planka_logger.warning(f"   ⚠️ Task {i+1} failed: {e}")

            planka_logger.info(f"🏷️ Assigning 'AUTO ADDED' label to the card...")
            # Get labels with timeout
            board_labels = run_with_timeout(lambda: target_board.labels, description="Get labels")
            planka_logger.debug(f"✅ Found {len(board_labels)} labels in board")

            auto_added_label = next((l for l in board_labels if l.name == "AUTO ADDED"), None)

            if not auto_added_label:
                planka_logger.info("🔧 'AUTO ADDED' label not found, creating it...")
                try:
                    auto_added_label = target_board.create_label(name="AUTO ADDED", color="lime-green")
                    planka_logger.info("✅ Created 'AUTO ADDED' label")
                except Exception as e:
                    planka_logger.warning(f"⚠️ Failed to create 'AUTO ADDED' label: {e}")

            if auto_added_label:
                planka_logger.info(f"🔖 Using label: '{auto_added_label.name}' (color: {auto_added_label.color})")
                try:
                    new_card.add_label(auto_added_label)
                    planka_logger.info(f"✅ Label '{auto_added_label.name}' assigned to card")
                except Exception as e:
                    planka_logger.warning(f"⚠️ Failed to assign label: {e}")

            planka_logger.info("🎉 Planka card with checklist and label created successfully!")

            success_log = f"PLANKAR CARD CREATED: {job_number} | {job_description} | Page {page_num + 1} | {os.path.basename(pdf_path)} | Card ID: {new_card.id} | {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
            with open(BAD_PART_LOG_FILE, 'a') as f:
                f.write(success_log)

            send_notification("Bad Part Alert", f"Bad Part Card Created: {job_number} - Planka card with checklist ready")

        except Exception as e_planka:
            planka_logger.error(f"❌ Failed to create Planka card: {e_planka}")

            log_entry = f"BAD PART DETECTED: {job_number} | {job_description} | Page {page_num + 1} | {os.path.basename(pdf_path)} | {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')} | MANUAL PLANKAR CARD NEEDED\n"
            log_entry += f"  💡 CREATE CARD: '{card_name}'\n"
            log_entry += f"  💡 ADD CHECKLIST:\n"
            for i, task in enumerate(tasks_data):
                log_entry += f"     - [ ] {task}\n"

            try:
                with open(BAD_PART_LOG_FILE, 'a') as f:
                    f.write(log_entry + "\n")
                planka_logger.info("✅ Bad part logged for manual Planka card creation")
                send_notification("Bad Part Alert", f"Bad Part Detected: {job_number} - Manual Planka card required")
            except Exception as e_log:
                planka_logger.error(f"Failed to write manual log: {e_log}")

    except Exception as e:
        planka_logger.error(f"❌ Critical error in bad parts processing: {e}")
        try:
            log_error = f"CRITICAL ERROR: Bad part processing failed - {str(e)}\n"
            with open(BAD_PART_LOG_FILE, 'a') as f:
                f.write(log_error)
        except Exception as e_log:
            planka_logger.error(f"Failed to log critical error: {e_log}")
