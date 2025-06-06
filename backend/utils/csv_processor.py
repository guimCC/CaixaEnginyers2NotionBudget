import csv
import io
import re
from datetime import datetime
import calendar
from typing import Dict, List, Any, Tuple, Optional
import os

# Import functions to get Notion entities
from .notionAPI import (
    list_accounts, list_expense_types, list_months, 
    list_income_types, list_subscriptions, list_debts
)

###################### TYPE OF MOVEMENTS ######################

MAIN_ACCOUNT = "Main Account Name" # Notion name of the main account
CARD = "TARGETA" # Entries starting with this (card tranasactions) are expenses
BIZUM_TO = "BIZUM A" # Entries starting with this (Bizum sent) are expenses
BIZUM_FROM = "BIZUM DE" # Entries starting with this (Bizum received) are transfers
TRANSFER = "TRASPAS" # Entries starting with this (transfer) are transfers, sometimes income
SALARY = "NOMINA" # Entries starting with this (salary) are income
INCOME = "INGRES" # Entries starting with this (income) are transfers, sometimes income

######################## KEYWORDS FOR CATEGORIZATION ######################
EXPENSE_KEYWORDS = {
    'lowercase_query': 'Tyoe',
}

INCOME_KEYWORDS = {
}

DEFAULT_EXPENSE_TYPE = None  # Will be set dynamically
DEFAULT_INCOME_TYPE = None   # Will be set dynamically
DEFAULT_ACCOUNT = None       # Will be set dynamically

def init_defaults():
    """Initialize default values from Notion data"""
    global DEFAULT_EXPENSE_TYPE, DEFAULT_INCOME_TYPE, DEFAULT_ACCOUNT
    
    # Get accounts
    accounts = list_accounts()
    # Set default account to Caixa Enginyers if exists, otherwise first account
    DEFAULT_ACCOUNT = next((acc for acc in accounts if acc["name"] == MAIN_ACCOUNT), 
                           accounts[0] if accounts else None)
    
    # Get expense types
    expense_types = list_expense_types()
    DEFAULT_EXPENSE_TYPE = expense_types[0] if expense_types else None
    
    # Get income types
    income_types = list_income_types()
    DEFAULT_INCOME_TYPE = income_types[0] if income_types else None

def process_csv(contents: bytes, original_filename: str) -> Dict:
    """Process CSV file contents and return categorized transactions"""
    # Initialize defaults if needed
    if DEFAULT_ACCOUNT is None:
        init_defaults()
    print("Processing CSV file...")
    # Parse CSV
    csv_text = contents.decode('utf-8-sig') # Use utf-8-sig to handle potential BOM
    csv_file_like = io.StringIO(csv_text)
    csv_reader = csv.DictReader(csv_file_like)
    
    # Ensure fieldnames are as expected, otherwise raise error or handle
    expected_headers = ['DATE', 'CONCEPT', 'IMPORT', 'LOADED']
    if not all(h in csv_reader.fieldnames for h in expected_headers):
        # Handle missing headers, e.g., raise ValueError or return error structure
        missing = [h for h in expected_headers if h not in csv_reader.fieldnames]
        raise ValueError(f"CSV is missing required headers: {', '.join(missing)}")
    
    # Get all required data from Notion for categorization
    accounts = list_accounts()
    expense_types = list_expense_types()
    income_types = list_income_types()
    months = list_months()
    subscriptions = list_subscriptions()
    debts = list_debts()

    # print("Loaded Notion data for categorization")
    # print(f"Accounts: {accounts}")
    # print(f"Expense Types: {expense_types}")
    # print(f"Income Types: {income_types}")
    # print(f"Months: {months}")
    # print(f"Subscriptions: {subscriptions}")
    # print(f"Debts: {debts}")
    
    
    processed_entries = []
    stats = {
        "total_rows_in_csv": 0,
        "processed_for_review": 0,
        "already_loaded_in_csv": 0,
        "expenses_found": 0,
        "incomes_found": 0,
        "transfers_found": 0,
        "unknown_type": 0,
    }
    
    all_csv_rows = list(csv_reader) # Read all rows to get total count and allow indexing
    print(f"Total rows in CSV: {len(all_csv_rows)}")
    stats["total_rows_in_csv"] = len(all_csv_rows)

    for i, row in enumerate(all_csv_rows):
        # Skip already loaded entries based on 'LOADED' column in the *uploaded* CSV
        # print("processing row", row)
        
        loaded_val = str(row.get('LOADED', '')).strip().lower()
        # print(f"Row {i} loaded value: {loaded_val}")
        
        if loaded_val == 'true' or loaded_val == '1':
            stats["already_loaded_in_csv"] += 1
            continue
        
        # print("Row not loaded, processing...")
        
        entry = categorize_transaction(
            row, 
            i, 
            accounts,           # Correct position
            expense_types, 
            income_types, 
            months, 
            subscriptions, 
            debts,
            original_filename   # Correct position - last parameter
        )
        # print(f"Categorized entry: {entry}")
        
        if entry:
            processed_entries.append(entry)
            entry_type = entry.get("type", "unknown_type")
            if entry_type in stats: # e.g. expenses_found
                 stats[f"{entry_type}s_found"] = stats.get(f"{entry_type}s_found", 0) + 1
            else:
                stats["unknown_type"] +=1

    # print(f"Processed {len(processed_entries)} entries for review")
    
    stats["processed_for_review"] = len(processed_entries)
    processed_entries.sort(key=lambda x: datetime.strptime(x["date"], "%Y-%m-%d"))
    
    # print(f"Final stats: {processed_entries}")
    
    return {
        "status": "success",
        "message": f"Successfully processed {len(processed_entries)} entries.",
        "entries": processed_entries,
        "stats": stats
    }

def categorize_transaction(
    row: Dict[str, str], 
    row_id: int,
    accounts: List[Dict[str, str]],
    expense_types: List[Dict[str, str]],
    income_types: List[Dict[str, str]],
    months: List[Dict[str, str]],
    subscriptions: List[Dict[str, str]],
    debts: List[Dict[str, str]],
    original_csv_filename: str
) -> Optional[Dict[str, Any]]:
    """Categorize a transaction row based on its content"""
    if not all(key in row for key in ['DATE', 'CONCEPT', 'IMPORT']):
        return None
    
    date = row['DATE']
    concept = row['CONCEPT']
    amount_str = row['IMPORT']
    try:
        # Use the new fix_number_format function instead of direct float conversion
        amount = fix_number_format(amount_str)
    except ValueError as e:
        print(f"Error parsing amount '{amount_str}': {e}")
        return None
    
    # Get month id from date
    month_id = get_month_from_date(date, months)
    
    # Find default account (Caixa Enginyers)
    default_account = next((acc for acc in accounts if acc["name"] == MAIN_ACCOUNT), 
                          accounts[0] if accounts else None)
    
    default_account_id = default_account["id"] if default_account else None
    
    # print("DATE", date)
    # Base transaction entry
    transaction = {
        "csv_row_index": row_id,  # Change from csv_row_id
        "original_csv_filename": original_csv_filename,  # Change from csv_filename
        "date": date,
        "concept": concept,
        "amount": str(abs(amount)),
        "account_id": default_account_id,
        "month_id": month_id
    }
    
    # DETERMINE TRANSACTION TYPE AND DETAILS
    
    # 1. Check for expense patterns
    if (concept.startswith(CARD) and amount < 0) or (concept.startswith(BIZUM_TO) and amount < 0):
        # It's an expense
        expense_type_id = find_expense_type(concept, expense_types)
        
        # Extract name from TARGETA concept
        name = ""
        if concept.startswith(CARD):
            # Example: "TARGETA *1234 ... RESTAURANT NAME"
            match = re.search(r'TARGETA \*\d+ (.*)', concept)
            if match:
                name = match.group(1)
        elif concept.startswith(BIZUM_TO):
            # Example: "BIZUM A: PERSON NAME"
            match = re.search(r'BIZUM A: (.*)', concept)
            if match:
                name = match.group(1)
        
        # If no name extracted, use concept
        if not name:
            name = concept
            
        return {
            **transaction,
            "type": "expense",
            "name": name,
            "expense_type_id": expense_type_id,
            "subscription_id": None,
            "debt_id": None,
            "split": False,
            "subs": False
        }
        
    # 2. Check for income patterns  
    elif (concept.startswith(TRANSFER) or concept.startswith(SALARY)) and amount > 0:
        # It's an income
        income_type_id = find_income_type(concept, income_types)
        
        return {
            **transaction,
            "type": "income",
            "name": concept,
            "income_type_id": income_type_id
        }
        
    # 3. Check for transfer patterns
    elif concept.startswith(BIZUM_FROM) and amount > 0 or concept.startswith(INCOME) and amount > 0:
        # It's a transfer (return)
        name = ""
        # Example: "BIZUM DE: PERSON NAME"
        match = re.search(r'BIZUM DE: (.*)', concept)
        if match:
            name = match.group(1)
        
        return {
            **transaction,
            "type": "transfer",
            "name": name if name else concept,
            "from_account_id": None,
            "from_saving_id": None,
            "to_account_id": default_account_id,
            "to_saving_id": None,
            "transfer_type": "Return"
        }
    
    # 4. Default case: unidentified transaction
    # Base decision on amount sign
    if amount < 0:
        # Negative amount, likely an expense
        return {
            **transaction,
            "type": "expense",
            "name": concept,
            "expense_type_id": None,
            "subscription_id": None,
            "debt_id": None,
            "split": False,
            "subs": False
        }
    else:
        # Positive amount, likely an income
        return {
            **transaction,
            "type": "income",
            "name": concept,
            "income_type_id": None
        }

def find_expense_type(concept: str, expense_types: List[Dict[str, str]]) -> Optional[str]:
    """Find appropriate expense type based on concept text"""
    concept_lower = concept.lower()
    
    # Check against keywords
    for keyword, category in EXPENSE_KEYWORDS.items():
        if keyword in concept_lower:
            # Find expense type ID matching this category
            matching_type = next((et for et in expense_types if et["name"].lower() == category.lower()), None)
            if matching_type:
                return matching_type["id"]
    
    # Default to None if no match
    return None

def find_income_type(concept: str, income_types: List[Dict[str, str]]) -> Optional[str]:
    """Find appropriate income type based on concept text"""
    concept_lower = concept.lower()
    
    # Check against keywords
    for keyword, category in INCOME_KEYWORDS.items():
        if keyword in concept_lower:
            # Find income type ID matching this category
            matching_type = next((it for it in income_types if it["name"].lower() == category.lower()), None)
            if matching_type:
                return matching_type["id"]
    
    # Default to None if no match
    return None

def get_month_from_date(date_str: str, months: List[Dict[str, str]]) -> Optional[str]:
    """Extract month from date string and find its ID"""
    try:
        # Assuming date_str is in format dd/mm/yyyy
        dt = datetime.strptime(date_str, "%Y-%m-%d")
        month_name = dt.strftime("%B")[:3]  # Full month name
        year = dt.strftime("%Y")[2:]
        
        # Format to match your month names pattern (e.g., "May 2025")
        formatted_month = f"{month_name} {year}"
        
        # Find matching month ID
        matching_month = next((m for m in months if m["name"] == formatted_month), None)
        if matching_month:
            return matching_month["id"]
        
        # Try just the month name
        matching_month = next((m for m in months if m["name"] == month_name), None)
        return matching_month["id"] if matching_month else None
        
    except Exception:
        return None

def fix_number_format(value_str: str) -> float:
    """
    Fix problematic number format with multiple decimal points.
    Example: converts "1.410.00" to 1410.00
    """
    # First replace any commas with dots for consistency
    value_str = value_str.replace(',', '.')
    
    # If there are multiple dots, treat all but the last as thousand separators
    parts = value_str.split(',')
    if len(parts) > 2:  # More than one dot exists
        integer_part = ''.join(parts[:-1])  # Join all parts except the last
        decimal_part = parts[-1]            # Last part is the decimal
        return float(f"{integer_part}.{decimal_part}")
    
    # Regular case with only one or no decimal point
    return float(value_str)