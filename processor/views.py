from django.shortcuts import render
from django.conf import settings
from django.http import FileResponse
from django.core.files.storage import FileSystemStorage
import os
import re
from pdf2image import convert_from_path
import cv2
import pytesseract
import numpy as np

# --- Helper function to sort contours ---
def sort_contours(cnts, method="top-to-bottom"):
    """Sorts contours based on their position."""
    reverse = False
    i = 0
    if method == "right-to-left" or method == "bottom-to-top":
        reverse = True
    if method == "top-to-bottom" or method == "bottom-to-top":
        i = 1
    
    boundingBoxes = [cv2.boundingRect(c) for c in cnts]
    (cnts, boundingBoxes) = zip(*sorted(zip(cnts, boundingBoxes),
        key=lambda b:b[1][i], reverse=reverse))
    
    return (cnts, boundingBoxes)

# --- The Main View Function ---
def upload_view(request):
    """
    Handles both the GET request for showing the form and the POST request for processing the PDF.
    """
    if request.method == 'GET':
        return render(request, 'processor/upload.html')

    if request.method == 'POST' and request.FILES.get('pdf_file'):
        pdf_file = request.FILES['pdf_file']
        output_filename = request.POST.get('output_filename', 'processed_data')
        
        # Ensure the output filename is safe
        output_filename = "".join([c for c in output_filename if c.isalpha() or c.isdigit() or c in (' ', '-')]).rstrip()
        output_txt_filename = f"{output_filename}.txt"

        # 1. SAVE THE UPLOADED PDF TEMPORARILY
        fs = FileSystemStorage()
        saved_pdf_name = fs.save(pdf_file.name, pdf_file)
        pdf_path = fs.path(saved_pdf_name)

        all_formatted_text = []

        try:
            # 2. CONVERT PDF TO A LIST OF IMAGES
            # On Windows, you might need to specify the poppler path:
            # images = convert_from_path(pdf_path, poppler_path=r"C:\path\to\poppler-xx\bin")
            images = convert_from_path(pdf_path, dpi=300)

            # 3. PROCESS EACH IMAGE (PAGE)
            for page_num, image in enumerate(images):
                # Convert Pillow image to OpenCV format
                open_cv_image = np.array(image) 
                img_color = cv2.cvtColor(open_cv_image, cv2.COLOR_RGB2BGR)
                img_gray = cv2.cvtColor(img_color, cv2.COLOR_BGR2GRAY)
                
                # 4. USE OPENCV TO FIND DATA BLOCKS ("Divide and Conquer")
                # Apply thresholding to get a binary image
                _, thresh = cv2.threshold(img_gray, 230, 255, cv2.THRESH_BINARY_INV)
                
                # Find contours
                contours, _ = cv2.findContours(thresh, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
                
                # Filter contours by area to get potential data blocks
                min_contour_area = 5000  # Adjust this value based on your PDF structure
                valid_contours = [cnt for cnt in contours if cv2.contourArea(cnt) > min_contour_area]
                
                # Sort contours from top to bottom to process in order
                (sorted_contours, bounding_boxes) = sort_contours(valid_contours)

                # 5. CROP, OCR, AND FORMAT EACH BLOCK
                for i, c in enumerate(sorted_contours):
                    x, y, w, h = bounding_boxes[i]
                    
                    # Add a small padding around the cropped area
                    padding = 10
                    cropped_image = img_color[y-padding:y+h+padding, x-padding:x+w+padding]

                    # Perform OCR on the small, cropped image
                    try:
                        text = pytesseract.image_to_string(cropped_image, lang='ben')
                        
                        # 6. CLEAN AND STRUCTURE THE EXTRACTED TEXT
                        # This part is crucial and may need adjustment for your specific PDF's text format
                        lines = [line.strip() for line in text.split('\n') if line.strip()]
                        
                        # Simple parsing logic (can be improved with RegEx)
                        data = {}
                        serial_num = f"{(i + 1):03d}" # Default serial if not found
                        
                        # Attempt to find the serial number in the first line
                        first_line_match = re.match(r'^(\S+)\s', lines[0])
                        if first_line_match:
                            serial_num_text = first_line_match.group(1).replace('.', '').replace('"', '')
                            if serial_num_text.isnumeric():
                                serial_num = serial_num_text

                        # Extract key-value pairs
                        for line in lines:
                            if ':' in line:
                                parts = line.split(':', 1)
                                key = parts[0].strip()
                                value = parts[1].strip()
                                
                                # Normalize keys
                                if 'নাম' in key: data['নাম'] = value
                                elif 'ভোটার' in key or 'Ïভাটার' in key: data['ভোটার নং'] = value
                                elif 'পিতা' in key or 'িপতা' in key: data['পিতা'] = value
                                elif 'মাতা' in key: data['মাতা'] = value
                                elif 'পেশা' in key or 'Ïপশা' in key: data['পেশা'] = value
                                elif 'জন্ম' in key: data['জন্ম তারিখ'] = value
                                elif 'ঠিকানা' in key or 'িঠকানা' in key: data['ঠিকানা'] = value

                        # Format the data into the desired string
                        formatted_entry = (
                            f"{serial_num}. নাম: {data.get('নাম', '')},\n"
                            f"ভোটার নং: {data.get('ভোটার নং', '')},\n"
                            f"পিতা: {data.get('পিতা', '')},\n"
                            f"মাতা: {data.get('মাতা', '')},\n"
                            f"পেশা: {data.get('পেশا', '')},\n"
                            f"জন্ম তারিখ: {data.get('জন্ম তারিখ', '')},\n"
                            f"ঠিকানা: {data.get('ঠিকানা', '')}\n"
                        )
                        all_formatted_text.append(formatted_entry)

                    except Exception as ocr_err:
                        print(f"Could not process a block: {ocr_err}")
                        continue
            
            # 7. CREATE AND SAVE THE FINAL .TXT FILE
            final_text_content = "\n".join(all_formatted_text)
            output_txt_path = os.path.join(settings.MEDIA_ROOT, output_txt_filename)
            
            with open(output_txt_path, 'w', encoding='utf-8') as f:
                f.write(final_text_content)
            
            # Pass the download URL to the template
            download_url = os.path.join(settings.MEDIA_URL, output_txt_filename)
            
            return render(request, 'processor/download.html', {'download_url': download_url, 'filename': output_txt_filename})

        finally:
            # 8. CLEANUP: Delete the temporary PDF file
            fs.delete(saved_pdf_name)

    return render(request, 'processor/upload.html', {'error': 'File upload failed.'})

