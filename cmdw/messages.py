from sys import exit
from click import secho, echo

def error_message(message: str, error_type:str="Error", exit_code: int = 1):
    """
    Print an error message to the console and exit the program.
    
    :param message: The error message to display.
    :param exit_code: The exit code to use when exiting the program.
    """
    secho(f"\n{error_type}: {message}", fg="red", err=True)
    exit(exit_code)
    
def warning_message(message: str):
    """
    Print a warning message to the console.
    
    :param message: The warning message to display.
    """
    secho(f"Warning: {message}", fg="yellow", err=True)

def info_message(message: str, left="Info"):
    """
    Print an informational message to the console.
    
    :param message: The informational message to display.
    :param left: The label for the informational message.
    """
    secho(f"{left}: {message}", fg="blue", err=True)
    
def success_message(message: str):
    """
    Print a success message to the console.
    
    :param message: The success message to display.
    """
    secho(f"Success: {message}\n", fg="green", err=True)
    
def title_message(message: str):
    """
    Print a title message to the console.
    
    :param message: The title message to display.
    """
    secho(f"\n{message}\n", fg="magenta", bold=True, err=True)
    
def message(content:str):
    echo(content)
    
