function inventreeDocReady() {
    /* Run this function when the HTML document is loaded.
     * This will be called for every page that extends "base.html"
     */

    window.addEventListener("dragover",function(e){
        e = e || event;
        e.preventDefault();
      },false);

    window.addEventListener("drop",function(e){
        e = e || event;
        e.preventDefault();
      },false);

    /* Add drag-n-drop functionality to any element
     * marked with the class 'dropzone'
     */
    $('.dropzone').on('dragenter', function(event) {

        // TODO - Only indicate that a drop event will occur if a file is being dragged
        var transfer = event.originalEvent.dataTransfer;

        if (true || isFileTransfer(transfer)) {
            $(this).addClass('dragover');
        }
    });

    $('.dropzone').on('dragleave drop', function(event) {
        $(this).removeClass('dragover');
    });

    // Callback to launch the 'About' window
    $('#launch-about').click(function() {
        var modal = $('#modal-about');

        modal.modal({
            backdrop: 'static',
            keyboard: 'false',
        });

        modal.modal('show');
    });
}

function isFileTransfer(transfer) {
    /* Determine if a transfer (e.g. drag-and-drop) is a file transfer 
     */

    return transfer.files.length > 0;
}


function isOnlineTransfer(transfer) {
    /* Determine if a drag-and-drop transfer is from another website.
     * e.g. dragged from another browser window
     */

    return transfer.items.length > 0;
}


function getImageUrlFromTransfer(transfer) {
    /* Extract external image URL from a drag-and-dropped image
     */

    var url = transfer.getData('text/html').match(/src\s*=\s*"(.+?)"/)[1];

    console.log('Image URL: ' + url);

    return url;
}


function enableDragAndDrop(element, url, options) {
    /* Enable drag-and-drop file uploading for a given element.
    
    Params:
        element - HTML element lookup string e.g. "#drop-div"
        url - URL to POST the file to
        options - object with following possible values:
            label - Label of the file to upload (default='file')
            success - Callback function in case of success
            error - Callback function in case of error
    */

    $(element).on('drop', function(event) {

        var transfer = event.originalEvent.dataTransfer;

        var label = options.label || 'file';

        var formData = new FormData();

        if (isFileTransfer(transfer)) {
            formData.append(label, transfer.files[0]);
            
            inventreeFormDataUpload(
                url,
                formData,
                {
                    success: function(data, status, xhr) {
                        console.log('Uploaded file via drag-and-drop');
                        if (options.success) {
                            options.success(data, status, xhr);
                        }
                    },
                    error: function(xhr, status, error) {
                        console.log('File upload failed');
                        if (options.error) {
                            options.error(xhr, status, error);
                        }
                    }
                }
            );
        } else {
            console.log('Ignoring drag-and-drop event (not a file)');
        }
    });
}

function imageHoverIcon(url) {
    /* Render a small thumbnail icon for an image.
     * On mouseover, display a full-size version of the image
     */

    if (!url) {
        url = '/static/img/blank_image.png';
    }

    var html = `
        <a class='hover-icon'>
            <img class='hover-img-thumb' src='` + url + `'>
            <img class='hover-img-large' src='` + url + `'>
        </a>
        `;

    return html;
}

function inventreeSave(name, value) {
    /*
     * Save a key:value pair to local storage
     */

    var key = "inventree-" + name;
    localStorage.setItem(key, value);
}

function inventreeLoad(name, defaultValue) {
    /* 
     * Retrieve a key:value pair from local storage
     */

    var key = "inventree-" + name;

    var value = localStorage.getItem(key);

    if (value == null) {
        return defaultValue;
    } else {
        return value;
    }
}

function inventreeLoadInt(name) {
    /*
     * Retrieve a value from local storage, and attempt to cast to integer
     */

    var data = inventreeLoad(name);

    return parseInt(data, 10);
}

function inventreeLoadFloat(name) {

    var data = inventreeLoad(name);

    return parseFloat(data);
}

function inventreeDel(name) {

    var key = 'inventree-' + name;

    localStorage.removeItem(key);
}