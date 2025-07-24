import os
import logging
from flask import Flask, render_template, request, jsonify, send_file, flash, redirect, url_for, session
from werkzeug.middleware.proxy_fix import ProxyFix
import pandas as pd
from scraper import PaymentDataScraper
import tempfile
import uuid
from urllib.parse import urlparse

# Configure logging
logging.basicConfig(level=logging.DEBUG)

# Create the app
app = Flask(__name__)
app.secret_key = os.environ.get("SESSION_SECRET", "dev-secret-key-change-in-production")
app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1, x_host=1)

# Initialize scraper
scraper = PaymentDataScraper()

# In-memory data store to avoid large session cookies
session_data_store = {}

def store_session_data(session_id, data):
    """Store data in server-side storage instead of session cookie"""
    import time
    # Clean old sessions (older than 2 hours)
    current_time = time.time()
    expired = [k for k, v in session_data_store.items() if current_time - v.get('timestamp', 0) > 7200]
    for k in expired:
        del session_data_store[k]
    
    session_data_store[session_id] = {
        'data': data,
        'timestamp': current_time
    }

def get_session_data(session_id):
    """Retrieve data from server-side storage"""
    stored = session_data_store.get(session_id)
    return stored['data'] if stored else None

@app.route('/')
def index():
    """Main page with URL input form."""
    return render_template('index.html')

@app.route('/scrape', methods=['POST'])
def scrape_url():
    """Handle URL scraping request."""
    url = request.form.get('url', '').strip()
    
    if not url:
        flash('Please enter a valid URL', 'error')
        return redirect(url_for('index'))
    
    # Validate URL format
    try:
        parsed_url = urlparse(url)
        if not parsed_url.scheme or not parsed_url.netloc:
            flash('Please enter a valid URL with http:// or https://', 'error')
            return redirect(url_for('index'))
    except Exception:
        flash('Invalid URL format', 'error')
        return redirect(url_for('index'))
    
    try:
        # Scrape the data
        app.logger.info(f"Starting scrape for URL: {url}")
        data = scraper.scrape_payment_data(url)
        
        if not data:
            flash('No payment data found on the specified page', 'warning')
            return redirect(url_for('index'))
        
        # Store data in server-side storage to avoid large session cookies
        session_id = str(uuid.uuid4())
        store_session_data(session_id, {
            'data': data,
            'url': url,
            'columns': list(data[0].keys()) if data else []
        })
        
        app.logger.info(f"Successfully scraped {len(data)} records")
        return redirect(url_for('preview', session_id=session_id))
        
    except Exception as e:
        app.logger.error(f"Error scraping URL {url}: {str(e)}")
        flash(f'Error scraping data: {str(e)}', 'error')
        return redirect(url_for('index'))

@app.route('/preview/<session_id>')
def preview(session_id):
    """Preview scraped data before download."""
    scraped_info = get_session_data(session_id)
    
    if not scraped_info:
        flash('Session expired or invalid. Please scrape again.', 'error')
        return redirect(url_for('index'))
    data = scraped_info['data']
    url = scraped_info['url']
    columns = scraped_info['columns']
    
    return render_template('preview.html', 
                         data=data, 
                         url=url, 
                         columns=columns,
                         session_id=session_id,
                         total_records=len(data))

@app.route('/update_data/<session_id>', methods=['POST'])
def update_data(session_id):
    """Update PassBook and Variance data from frontend."""
    scraped_info = get_session_data(session_id)
    
    if not scraped_info:
        return jsonify({'error': 'Session expired'}), 400
    
    try:
        update_data = request.get_json()
        data = scraped_info['data']
        
        # Update the data using row IDs instead of indices
        for row_id, updates in update_data.items():
            # Find the row with matching ID
            for row in data:
                if row.get('_row_id') == row_id:
                    for key, value in updates.items():
                        if key in ['Principal_PassBook', 'Principal_Variance', 'Principal_Remarks', 'CBU_PassBook', 'CBU_Variance', 'CBU_Remarks', 'CBU_withdraw_PassBook', 'CBU_withdraw_Variance', 'CBU_withdraw_Remarks']:
                            row[key] = str(value)
                    break
        
        # Update server-side storage
        store_session_data(session_id, scraped_info)
        return jsonify({'success': True})
        
    except Exception as e:
        app.logger.error(f"Error updating data: {str(e)}")
        return jsonify({'error': str(e)}), 500

@app.route('/download/<session_id>')
def download_excel(session_id):
    """Generate and download Excel file."""
    scraped_info = get_session_data(session_id)
    
    if not scraped_info:
        flash('Session expired or invalid. Please scrape again.', 'error')
        return redirect(url_for('index'))
    
    try:
        data = scraped_info['data']
        url = scraped_info['url']
        
        # Create DataFrame with proper column ordering
        column_order = ['Receipt No', 'Date', 'Principal', 'Pen', 'Principal_PassBook', 'Principal_Variance', 'Principal_Remarks',
                       'CBU', 'CBU_PassBook', 'CBU_Variance', 'CBU_Remarks', 'CBU withdraw', 'CBU_withdraw_PassBook', 'CBU_withdraw_Variance', 'CBU_withdraw_Remarks', 'Collector']
        
        # Ensure all columns exist in the data
        for row in data:
            for col in column_order:
                if col not in row:
                    row[col] = ''
        
        # Create DataFrame and reorder columns
        df = pd.DataFrame(data)
        
        # Reorder columns to match our preferred order
        available_columns = [col for col in column_order if col in df.columns]
        if available_columns:
            df = df[available_columns]
        
        # Create temporary file
        temp_file = tempfile.NamedTemporaryFile(delete=False, suffix='.xlsx')
        
        # Write to Excel with formatting
        with pd.ExcelWriter(temp_file.name, engine='openpyxl') as writer:
            df.to_excel(writer, sheet_name='Payment Data', index=False)
            
            # Get workbook and worksheet for formatting
            workbook = writer.book
            worksheet = writer.sheets['Payment Data']
            
            # Auto-adjust column widths
            for column in worksheet.columns:
                max_length = 0
                column_letter = column[0].column_letter
                for cell in column:
                    try:
                        if len(str(cell.value)) > max_length:
                            max_length = len(str(cell.value))
                    except:
                        pass
                adjusted_width = min(max_length + 2, 50)
                worksheet.column_dimensions[column_letter].width = adjusted_width
        
        # Generate filename
        domain = urlparse(url).netloc.replace('www.', '')
        filename = f"payment_data_{domain}_{session_id[:8]}.xlsx"
        
        app.logger.info(f"Generated Excel file for session {session_id}")
        
        # Clean up server-side data after download
        session_data_store.pop(session_id, None)
        
        return send_file(temp_file.name, 
                        as_attachment=True, 
                        download_name=filename,
                        mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
        
    except Exception as e:
        app.logger.error(f"Error generating Excel file: {str(e)}")
        flash(f'Error generating Excel file: {str(e)}', 'error')
        return redirect(url_for('index'))

@app.route('/add_row/<session_id>', methods=['POST'])
def add_row(session_id):
    """Add a new row and sort by date."""
    scraped_info = get_session_data(session_id)
    
    if not scraped_info:
        return jsonify({'error': 'Session expired'}), 400
    
    try:
        new_row_data = request.get_json()
        data = scraped_info['data']
        
        # Add unique ID to the new row to prevent conflicts after sorting
        import time
        new_row_data['_row_id'] = f"row_{int(time.time() * 1000)}"
        
        # Add the new row to the data
        data.append(new_row_data)
        
        # Sort by date (parse dates properly)
        def parse_date(date_str):
            try:
                from datetime import datetime as dt
                import re
                
                if not date_str or not isinstance(date_str, str):
                    return dt(1900, 1, 1)
                
                date_str = date_str.strip()
                
                # Skip non-date strings like "April", "total", etc.
                if not re.search(r'\d', date_str):
                    return dt(1900, 1, 1)
                
                # Primary format should be MM/DD/YYYY to match our data
                formats = [
                    '%m/%d/%Y',     # MM/DD/YYYY (primary format)
                    '%m-%d-%Y',     # MM-DD-YYYY
                    '%d/%m/%Y',     # DD/MM/YYYY (fallback)
                    '%d-%m-%Y',     # DD-MM-YYYY (fallback)
                    '%Y-%m-%d',     # YYYY-MM-DD (fallback)
                    '%Y/%m/%d',     # YYYY/MM/DD (fallback)
                ]
                
                for fmt in formats:
                    try:
                        parsed_date = dt.strptime(date_str, fmt)
                        app.logger.debug(f"Successfully parsed date '{date_str}' with format '{fmt}' -> {parsed_date}")
                        return parsed_date
                    except ValueError:
                        continue
                
                # If no format works, return a very old date to put it at the beginning
                app.logger.warning(f"Could not parse date: '{date_str}', placing at beginning")
                return dt(1900, 1, 1)
            except Exception as e:
                app.logger.error(f"Error parsing date '{date_str}': {str(e)}")
                from datetime import datetime as dt
                return dt(1900, 1, 1)
        
        # Sort data by date
        data.sort(key=lambda x: parse_date(x.get('Date', '')))
        
        # Update server-side storage with sorted data
        scraped_info['data'] = data
        store_session_data(session_id, scraped_info)
        
        app.logger.info(f"Added new row and sorted data. Total records: {len(data)}")
        return jsonify({'success': True, 'total_records': len(data)})
        
    except Exception as e:
        app.logger.error(f"Error adding row: {str(e)}")
        return jsonify({'error': str(e)}), 500

@app.route('/delete_row/<session_id>', methods=['POST'])
def delete_row(session_id):
    """Delete a row by row ID."""
    scraped_info = get_session_data(session_id)
    
    if not scraped_info:
        return jsonify({'error': 'Session expired'}), 400
    
    try:
        request_data = request.get_json()
        row_id = request_data.get('row_id')
        
        if not row_id:
            return jsonify({'error': 'Row ID required'}), 400
        
        data = scraped_info['data']
        
        # Find and remove the row with matching ID
        original_length = len(data)
        scraped_info['data'] = [row for row in data if row.get('_row_id') != row_id]
        
        if len(scraped_info['data']) == original_length:
            return jsonify({'error': 'Row not found'}), 404
        
        # Update server-side storage
        store_session_data(session_id, scraped_info)
        
        app.logger.info(f"Deleted row {row_id}. Total records: {len(scraped_info['data'])}")
        return jsonify({'success': True, 'total_records': len(scraped_info['data'])})
        
    except Exception as e:
        app.logger.error(f"Error deleting row: {str(e)}")
        return jsonify({'error': str(e)}), 500

@app.route('/health')
def health_check():
    """Health check endpoint."""
    return jsonify({'status': 'healthy', 'service': 'payment-data-scraper'})

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)
