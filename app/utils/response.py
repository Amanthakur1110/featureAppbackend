from flask import jsonify


def success_response(data=None, message="Success", status_code=200):
    """
    Standard envelope matching Android FeatureApp Response<T>:
    {
        "success": true,
        "message": message,
        "data": data
    }
    """
    response_body = {
        "success": True,
        "message": message,
        "data": data
    }
    return jsonify(response_body), status_code


def error_response(message="An error occurred", data=None, status_code=400):
    """
    Standard error envelope matching Android FeatureApp Response<T>:
    {
        "success": false,
        "message": message,
        "data": data
    }
    """
    response_body = {
        "success": False,
        "message": message,
        "data": data
    }
    return jsonify(response_body), status_code
