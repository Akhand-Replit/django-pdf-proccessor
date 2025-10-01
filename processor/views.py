from django.shortcuts import render
from django.conf import settings
from django.http import FileResponse
from django.core.files.storage import FileSystemStorage
import os
import re
from pdf2image import convert_from_path
import pytesseract
import numpy as np
import pandas as pd

# --- Add this line to specify the Tesseract path ---
pytesseract.pytesseract.tesseract_cmd = r'C:\Program Files\Tesseract-OCR\tesseract.exe'

def parse_voter_block(text_block):
    """
    Parses a clean block of text for a single voter using a robust,
    keyword-slicing approach, which is more resilient to OCR errors than regex.
    """
    data = {}
    
    # A list of all possible keywords we want to extract.
    keywords = ['নাম', 'ভোটার নং', 'পিতা', 'মাতা', 'পেশা', 'জন্ম তারিখ', 'ঠিকানা']
    
    # Find the start position of each keyword in the text block.
    found_keywords = []
    for keyword in keywords:
        # Use a simple find; it's faster and sufficient here.
        # We search for the keyword itself to be more robust against OCR errors like 'ভোটার:' vs 'ভোটার'.
        start_index = text_block.find(keyword)
        if start_index != -1:
            found_keywords.append({'keyword': keyword, 'start': start_index})
            
    # Sort the found keywords by their position in the text.
    found_keywords.sort(key=lambda x: x['start'])
    
    # Extract the text between each keyword and the next.
    for i, found in enumerate(found_keywords):
        keyword = found['keyword']
        
        # The value starts right after the keyword.
        start_pos = found['start'] + len(keyword)
        
        # The value ends where the next keyword begins.
        end_pos = None
        if i + 1 < len(found_keywords):
            end_pos = found_keywords[i+1]['start']
            
        # Extract the slice of text.
        value = text_block[start_pos:end_pos]
        
        # Clean up the extracted value by removing colons, newlines, and extra spaces.
        value = value.replace(':', '').replace('\n', ' ').strip()
        
        # A common issue is 'পেশা' and 'জন্ম তারিখ' are on the same line.
        # We need to handle this special case.
        if keyword == 'পেশা' and 'জন্ম তারিখ' in value:
            # Split the value at the 'জন্ম তারিখ' keyword.
            parts = value.split('জন্ম তারিখ')
            data['পেশা'] = parts[0].strip()
            if len(parts) > 1:
                # The rest of the string is the birth date.
                birth_date_text = parts[1].strip()
                # Find the date within the remaining text.
                birth_date_match = re.search(r'([\d/]+)', birth_date_text)
                if birth_date_match:
                    data['জন্ম তারিখ'] = birth_date_match.group(1)
            continue # Move to the next keyword

        data[keyword] = value

    return data


def upload_view(request):
    """
    Handles file uploads and processes the PDF using a spatial, column-aware approach.
    """
    if request.method == 'GET':
        return render(request, 'processor/upload.html')

    if request.method == 'POST' and request.FILES.get('pdf_file'):
        pdf_file = request.FILES['pdf_file']
        output_filename = request.POST.get('output_filename', 'processed_data')
        
        output_filename = "".join([c for c in output_filename if c.isalpha() or c.isdigit() or c in (' ', '-')]).rstrip()
        output_txt_filename = f"{output_filename}.txt"

        fs = FileSystemStorage()
        saved_pdf_name = fs.save(pdf_file.name, pdf_file)
        pdf_path = fs.path(saved_pdf_name)

        all_formatted_text = []

        try:
            poppler_bin_path = r"C:\poppler-25.07.0\Library\bin"
            images = convert_from_path(pdf_path, dpi=300, poppler_path=poppler_bin_path)

            for page_num, image in enumerate(images):
                print(f"--- Processing Page {page_num + 1} ---")
                
                ocr_df = pytesseract.image_to_data(image, lang='ben', output_type=pytesseract.Output.DATAFRAME)
                ocr_df = ocr_df.dropna().reset_index(drop=True)
                ocr_df = ocr_df[ocr_df['conf'] > 30]
                
                if ocr_df.empty:
                    continue

                page_width = image.width
                col_1_end = page_width / 3
                col_2_end = page_width * 2 / 3

                anchors = []
                for idx, row in ocr_df.iterrows():
                    text = str(row['text']).strip()
                    if re.match(r'^[০-৯o-۹]{1,3}\.', text):
                        x, y = row['left'], row['top']
                        col = 1 if x < col_1_end else 2 if x < col_2_end else 3
                        anchors.append({'col': col, 'x': x, 'y': y, 'text': text})
                
                anchors.sort(key=lambda a: (a['col'], a['y']))

                voter_boxes = []
                for i, anchor in enumerate(anchors):
                    next_anchor_y = image.height
                    for next_anchor in anchors[i+1:]:
                        if next_anchor['col'] == anchor['col']:
                            next_anchor_y = next_anchor['y']
                            break
                    
                    col_start = 0 if anchor['col'] == 1 else col_1_end if anchor['col'] == 2 else col_2_end
                    col_end = col_1_end if anchor['col'] == 1 else col_2_end if anchor['col'] == 2 else page_width

                    voter_boxes.append({
                        'serial': anchor['text'],
                        'box': (col_start, anchor['y'], col_end, next_anchor_y)
                    })

                for voter in voter_boxes:
                    box = voter['box']
                    box_df = ocr_df[
                        (ocr_df['left'] >= box[0]) & (ocr_df['left'] < box[2]) &
                        (ocr_df['top'] >= box[1]) & (ocr_df['top'] < box[3])
                    ]
                    
                    if box_df.empty:
                        continue
                        
                    block_text = ' '.join(box_df['text'].astype(str))
                    
                    if 'মাইগ্রেট' in block_text:
                        continue

                    voter_data = parse_voter_block(block_text)
                    
                    if voter_data.get('নাম'):
                        formatted_entry = (
                            f"{voter['serial']} নাম: {voter_data.get('নাম', '')},\n"
                            f"ভোটার নং: {voter_data.get('ভোটার নং', '')},\n"
                            f"পিতা: {voter_data.get('পিতা', '')},\n"
                            f"মাতা: {voter_data.get('মাতা', '')},\n"
                            f"পেশা: {voter_data.get('পেশা', '')},\n"
                            f"জন্ম তারিখ: {voter_data.get('জন্ম তারিখ', '')},\n"
                            f"ঠিকানা: {voter_data.get('ঠিকানা', '')}"
                        )
                        all_formatted_text.append(formatted_entry)

            final_text_content = "\n\n".join(all_formatted_text)
            output_txt_path = os.path.join(settings.MEDIA_ROOT, output_txt_filename)
            
            with open(output_txt_path, 'w', encoding='utf-8') as f:
                f.write(final_text_content)
            
            download_url = os.path.join(settings.MEDIA_URL, output_txt_filename)
            
            return render(request, 'processor/download.html', {'download_url': download_url, 'filename': output_txt_filename})

        except Exception as e:
            print(f"An error occurred: {e}")
            return render(request, 'processor/upload.html', {'error': f'An error occurred during processing: {e}'})

        finally:
            if fs.exists(saved_pdf_name):
                fs.delete(saved_pdf_name)

    return render(request, 'processor/upload.html', {'error': 'File upload failed.'})

