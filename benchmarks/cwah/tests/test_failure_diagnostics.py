from benchmarks.cwah.failure_diagnostics import classify_failure_message, failure_reason_counts_from_process_output


def test_classify_failure_message_known_coela_failures():
    assert classify_failure_message("PROCESS PUT: Not found source object: chair") == "not_found_source_object"
    assert classify_failure_message("PROCESS PUT: Not found object: sink") == "not_found_object"
    assert classify_failure_message("AssertionError: Error: Object already open") == "already_open"
    assert classify_failure_message("EXECUTION_GENERAL: Script is impossible to execute") == "script_impossible"
    assert classify_failure_message("execution_failed") == "execution_failed"


def test_failure_reason_counts_from_process_output_reads_message_lines():
    output = """
NO SUCCESS
{'0': {'message': 'ScriptExcutor 0: PROCESS PUT: Not found object: sink\nEXECUTION_GENERAL: Script is impossible to execute\n\n'}}
Traceback
AssertionError: Error: Object already open
"""

    assert failure_reason_counts_from_process_output(output) == {
        "already_open": 1,
        "not_found_object": 1,
    }
