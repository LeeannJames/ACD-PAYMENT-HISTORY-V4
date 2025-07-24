import requests
from bs4 import BeautifulSoup
import re
import logging
from urllib.parse import urljoin, urlparse
import time
from typing import List, Dict, Any, Optional

class PaymentDataScraper:
    """Web scraper for extracting specific payment data columns: Date, Pen, Principal, CBU, CBU withdraw, Collector."""
    
    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
        })
        self.timeout = 30
        self.logger = logging.getLogger(__name__)
        
        # Target columns we want to extract
        self.target_columns = ['Receipt No', 'Date', 'Principal', 'Pen', 'CBU', 'CBU withdraw', 'Collector']
        # Additional columns for user input and calculations
        self.calculated_columns = ['Principal_PassBook', 'Principal_Variance', 'CBU_PassBook', 'CBU_Variance', 'CBU_withdraw_PassBook', 'CBU_withdraw_Variance']
    
    def scrape_payment_data(self, url: str) -> List[Dict[str, Any]]:
        """
        Scrape payment data from a given URL, focusing on specific columns:
        Date, Pen, Principal, CBU, CBU withdraw, Collector
        
        Args:
            url: The URL to scrape
            
        Returns:
            List of dictionaries containing payment data
        """
        try:
            self.logger.info(f"Fetching URL: {url}")
            response = self.session.get(url, timeout=self.timeout)
            response.raise_for_status()
            
            soup = BeautifulSoup(response.content, 'html.parser')
            
            # Extract data from tables (primary method)
            payment_data = self._extract_from_tables(soup)
            
            # If no data found in tables, try other methods
            if not payment_data:
                payment_data.extend(self._extract_from_structured_content(soup))
            
            # Don't remove duplicates - preserve all records including date duplicates
            unique_data = payment_data
            
            # Add calculated columns with default values and unique IDs
            import time
            for i, record in enumerate(unique_data):
                # Add unique row ID to prevent conflicts after sorting
                record['_row_id'] = f"scraped_{int(time.time() * 1000)}_{i}"
                for calc_col in self.calculated_columns:
                    if calc_col not in record:
                        record[calc_col] = '0'
            
            self.logger.info(f"Extracted {len(unique_data)} payment records")
            return unique_data
            
        except requests.RequestException as e:
            self.logger.error(f"Request error for {url}: {str(e)}")
            raise Exception(f"Failed to fetch URL: {str(e)}")
        except Exception as e:
            self.logger.error(f"Scraping error for {url}: {str(e)}")
            raise Exception(f"Failed to scrape data: {str(e)}")
    
    def _extract_from_tables(self, soup: BeautifulSoup) -> List[Dict[str, Any]]:
        """Extract payment data from HTML tables."""
        data = []
        
        # Look for all tables
        tables = soup.find_all('table')
        
        for table in tables:
            table_data = self._process_table(table)
            if table_data:
                data.extend(table_data)
        
        return data
    
    def _process_table(self, table) -> List[Dict[str, Any]]:
        """Process a single table and extract relevant data."""
        data = []
        
        # Check if this table contains payment data by looking for specific patterns
        table_text = table.get_text().lower()
        if not self._is_payment_table(table_text):
            return data
        
        # Find header row
        header_row = None
        headers = []
        
        # Try to find headers in th tags first, then bold text, then first row
        for row in table.find_all('tr'):
            th_cells = row.find_all('th')
            if th_cells:
                headers = [self._clean_text(cell.get_text()) for cell in th_cells]
                header_row = row
                break
            
            # Check for bold headers in td tags (common in this type of page)
            td_cells = row.find_all('td')
            if td_cells:
                bold_count = sum(1 for cell in td_cells if cell.find('b') or 'font-weight:bold' in str(cell))
                if bold_count > 0 or self._contains_header_keywords([self._clean_text(cell.get_text()) for cell in td_cells]):
                    headers = [self._clean_text(cell.get_text()) for cell in td_cells]
                    header_row = row
                    break
        
        if not headers:
            return data
        
        # Map headers to our target columns
        column_mapping = self._map_headers_to_targets(headers)
        
        # Only process tables that have at least 3 of our target columns
        if sum(1 for v in column_mapping.values() if v) < 3:
            return data
        
        # Extract data rows
        all_rows = table.find_all('tr')
        data_rows = all_rows[1:] if header_row else all_rows
        
        for row in data_rows:
            cells = row.find_all(['td', 'th'])
            if len(cells) == len(headers):
                row_data = {}
                
                for i, cell in enumerate(cells):
                    cell_text = self._clean_text(cell.get_text())
                    target_column = column_mapping.get(i)
                    
                    if target_column and cell_text and cell_text != '':
                        row_data[target_column] = cell_text
                
                # Only add row if it has at least 3 target columns with data and is not a summary row
                if len(row_data) >= 3 and self._is_valid_data_row(row_data):
                    # Add empty PassBook columns for Principal/Pen, CBU, and CBU withdraw
                    row_data['Principal_PassBook'] = ''
                    row_data['Principal_Variance'] = ''
                    row_data['CBU_PassBook'] = ''
                    row_data['CBU_Variance'] = ''
                    row_data['CBU_withdraw_PassBook'] = ''
                    row_data['CBU_withdraw_Variance'] = ''
                    data.append(row_data)
        
        return data
    
    def _is_payment_table(self, table_text: str) -> bool:
        """Check if table contains payment transaction data."""
        payment_indicators = ['receipt', 'date', 'principal', 'collector', 'pen', 'cbu', 'payment', 'amount paid']
        return sum(1 for indicator in payment_indicators if indicator in table_text) >= 4
    
    def _contains_header_keywords(self, headers: List[str]) -> bool:
        """Check if headers contain our target keywords."""
        header_text = ' '.join(headers).lower()
        return any(target.lower() in header_text for target in self.target_columns)
    
    def _is_valid_data_row(self, row_data: Dict[str, str]) -> bool:
        """Check if this is a valid data row and not a summary/total row."""
        # Check all values for summary indicators
        all_values = ' '.join(str(v).lower() for v in row_data.values() if v)
        
        # Skip rows that contain summary/total keywords
        summary_keywords = ['total', 'subtotal', 'grand total', 'sum', 'summary', 'balance']
        if any(keyword in all_values for keyword in summary_keywords):
            return False
        
        # Skip rows with only month names (like "April", "May", etc.)
        month_names = ['january', 'february', 'march', 'april', 'may', 'june', 
                      'july', 'august', 'september', 'october', 'november', 'december',
                      'jan', 'feb', 'mar', 'apr', 'may', 'jun', 'jul', 'aug', 'sep', 'oct', 'nov', 'dec']
        if any(month in all_values for month in month_names) and len(row_data) < 4:
            return False
        
        # Check if the row has a valid date format
        date_value = row_data.get('Date', '').strip()
        if date_value:
            # Should look like a date (contains numbers and separators)
            import re
            # More flexible date pattern that allows various formats
            date_patterns = [
                r'\d+[/\-\.]\d+[/\-\.]\d+',  # DD/MM/YYYY, MM-DD-YYYY etc
                r'\d{4}[/\-\.]\d{1,2}[/\-\.]\d{1,2}',  # YYYY-MM-DD
                r'\d{1,2}\s+\w{3,9}\s+\d{4}',  # DD Month YYYY
                r'\w{3,9}\s+\d{1,2},?\s+\d{4}',  # Month DD, YYYY
            ]
            if not any(re.search(pattern, date_value) for pattern in date_patterns):
                # If it doesn't look like a proper date, skip it
                return False
        
        return True
    
    def _map_headers_to_targets(self, headers: List[str]) -> Dict[int, str]:
        """Map table header indices to target column names."""
        mapping = {}
        
        for i, header in enumerate(headers):
            header_lower = header.lower().strip()
            
            # Exact mapping for the payment table structure
            if header_lower == 'date':
                mapping[i] = 'Date'
            elif header_lower in ['receipt no', 'receipt', 'receipt number', 'ref no', 'reference', 'transaction id']:
                mapping[i] = 'Receipt No'
            elif header_lower == 'principal':
                mapping[i] = 'Principal'
            elif header_lower == 'pen':
                mapping[i] = 'Pen'
            elif header_lower == 'cbu':
                mapping[i] = 'CBU'
            elif header_lower == 'cbu withdraw':
                mapping[i] = 'CBU withdraw'
            elif header_lower == 'collector':
                mapping[i] = 'Collector'
            # Handle variations
            elif 'date' in header_lower and len(header_lower) <= 10:
                mapping[i] = 'Date'
            elif header_lower in ['penalty', 'pen', 'denda'] or (header_lower == 'pen' and len(header) <= 5):
                mapping[i] = 'Pen'
            elif 'principal' in header_lower or 'pokok' in header_lower:
                mapping[i] = 'Principal'
            elif header_lower.startswith('cbu'):
                if 'withdraw' in header_lower or 'tarik' in header_lower:
                    mapping[i] = 'CBU withdraw'
                else:
                    mapping[i] = 'CBU'
            elif 'collector' in header_lower or 'kolektor' in header_lower:
                mapping[i] = 'Collector'
        
        return mapping
    
    def _extract_from_structured_content(self, soup: BeautifulSoup) -> List[Dict[str, Any]]:
        """Extract data from structured div/span elements as fallback."""
        data = []
        
        # Look for divs or spans that might contain payment data
        elements = soup.find_all(['div', 'span', 'p'])
        
        current_record = {}
        
        for element in elements:
            text = self._clean_text(element.get_text())
            
            # Try to extract key-value pairs
            if ':' in text:
                parts = text.split(':', 1)
                if len(parts) == 2:
                    key = parts[0].strip()
                    value = parts[1].strip()
                    
                    # Map to target columns
                    for target in self.target_columns:
                        if target.lower() in key.lower():
                            current_record[target] = value
                            break
            
            # If we have a complete record, add it
            if len(current_record) >= 2:  # At least 2 fields
                data.append(current_record.copy())
                current_record = {}
        
        # Add any remaining record
        if current_record:
            data.append(current_record)
        
        return data
    
    def _clean_text(self, text: str) -> str:
        """Clean and normalize extracted text."""
        if not text:
            return ""
        
        # Remove extra whitespace and normalize
        text = ' '.join(text.split())
        
        # Remove common unwanted characters but keep useful ones
        text = re.sub(r'[^\w\s\$€£¥.,:\-/()%]', '', text)
        
        return text.strip()
    
    def _remove_duplicates(self, data: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Remove duplicate records while preserving order."""
        seen = set()
        unique_data = []
        
        for item in data:
            # Create a signature for the item
            item_signature = tuple(sorted(item.items()))
            
            if item_signature not in seen:
                seen.add(item_signature)
                unique_data.append(item)
        
        return unique_data