"""
########################################################################################################################
System-wide thread interface utilities for GUI

Taken from Programming Python by Mark Lutz and modified by Alek Dudko to support returning values from threaded
functions

Implements a single thread callback queue and checker timer loop shared by all the windows in a a program;
worker threads queue their exit and progress actions to be run in the main thread; tis doesn't block the GUI - ut
just spawns operations and manages and dispatches exits and progress; worker threads can overlap freely with the main
thread, and with other workers.

Using a queue of callback functions and arguments is more useful than a simple data queue if there can be many kinds of
threads running at the same time - each kind may have different implied exit actions.

Because Tkinter GUI API is not completely thread-safe, instead of calling GUI update callbacks directly after
thread main action, place them on a shared queue, to be run from a timer loop in the main thread, not a child thread;
this also makes GUI update points less random and unpredictable; requires threads to be split into main actions, and
progress action.

Assumes threaded action raises an exception on failure, and has a 'progress' callback argument if it supports
progress updates; also assumes callbacks are either short-lived or update as they run, and that queue will contain
callback functions (or other callables) for use in a GUI app - requires a widget in order to schedule and catch
'after' event loop callbacks; to use this model in non-GUI contexts, could use simple thread timer instead.

########################################################################################################################
"""


import sys

if sys.version_info >= (3, 0):  # if python 3.x
    import queue

    try:
        import _thread as thread

    except ImportError:  # dummy interface
        import _dummy_thread as thread

else:  # if python 2.x
    import Queue as queue

    try:
        import thread

    except ImportError:  # dummy interface
        import dummy_thread as thread


# infinite size queues for threads and data
thread_queue = queue.Queue(maxsize=0)
data_queue = queue.Queue(maxsize=0)


class ThreadCounter(object):
    """
    Easy, thread safe interface to be able to count how many threads are running.

    Book excerpt:

    ####################################################################################################################
    a thread-safe counter or flag: useful to avoid operation overlap if threads update other shared state beyond that
    managed by the thread callback queue
    ####################################################################################################################

    """

    def __init__(self):
        self.count = 0
        self.mutex = thread.allocate_lock()

    def incr(self):
        """Increment the number of threads that are running.

        Implement a safe way to increment the counter of threads.

        :return: None

        """

        self.mutex.acquire()
        self.count += 1
        self.mutex.release()

    def decr(self):
        """Decrement the number of threads that are running.

        Implement a safe way to decrement the counter of threads.

        :return: None

        """
        self.mutex.acquire()
        self.count -= 1
        self.mutex.release()

    def __len__(self):
        """Return the current count.

        :return: int, current count

        """

        return self.count


def thread_checker(widget, delay_msecs=100, per_event=1):
    """Check the thread queue for callbacks to execute in the main thread.

    Since Tkinter is not thread-safe (only main thread should be updating the GUI), implement a queue onto which
    various callbacks get placed by threaded functions. Main thread checks the queue and executes the callbacks.

    Book excerpt:

    ####################################################################################################################
    IN MAIN THREAD - periodically check thread completions queue; run implied GUI actions on queue in this main GUI
    thread; one consumer (GUI), and multiple producers (load, del, send) ; a simple list may suffice too: list.append
    and pop.atomic Runs at most N actions per timer event: looping through all queued callbacks on each timer event may
    block GUI indefinitely, but running only one can take a long time or consume CPU for timer events (e.g., progress);
    assumes callback is either short-lived or updates display as it runs: after a callback run, the code here
    reschedules and returns to event loop and updates; because this perpetual loop runs in main thread,
    does not stop program exit

    ####################################################################################################################

    :param widget: widget object, parent widget, usually Tk() instance
    :param delay_msecs: int, represents milliseconds between checks
    :param per_event: int, represent how many callbacks to read for each check
    :return: None

    """

    for i in range(per_event):
        try:
            (callback, args) = thread_queue.get(block=False)

        except queue.Empty:
            break

        else:
            callback(*args)

    widget.after(delay_msecs, lambda: thread_checker(widget, delay_msecs, per_event))


def data_thread_checker(widget, view, delay_msecs=100, per_event=1):
    """Check the data queue for data returned by threaded functions.

    In order to asynchronously obtain returned valued by threaded functions we must implement another queue.
    Once the threaded function is finished executing, the result gets put into this queue.

    Queue contains returned data and the context to help identify which thread/functionality has produced the result.
    Data will have to be parsed.

    :param widget: widget object, represents the main thread object, object that shall be constantly checking the queue
    :param view: view object, particular view that shall parse the data and decide what to do with it
    :param delay_msecs: int, represents milliseconds between checks
    :param per_event: int, represent how many callbacks to read for each check
    :return: None

    """

    for i in range(per_event):
        try:
            (data, context) = data_queue.get(block=False)

        except queue.Empty:
            break

        else:
            view.process_data_queue(data, context)

    widget.after(delay_msecs, lambda: data_thread_checker(widget, view, delay_msecs, per_event))


def threaded(action, args, context, on_exit, on_fail, on_progress, do_return=False):
    """Launch a function in a new thread that communicates with main thread via a shared thread queue.

    Perform the actual functionality in a new thread. Based on the outcome of the action, perform additional
    functionality, ie place resulting callbacks to be executed by main thread in the thread queue
    also place the result of action into the data queue.

    If no on_progress function was specified then simply execute the action function in this thread, once done
    put the results in the shared queue to be executed by GUI. If exception is raised put

    :param action: function object, function to perform
    :param args: tuple, arguments to the action function
    :param context: string, some context identifier to know which thread is working
    :param on_exit: function object, what to do when thread is done
    :param on_fail: function object, what to do when exception is raised
    :param on_progress: function object, how to update the GUI while thread is running
    :param do_return: boolean, if True action return value gets put into the data queue
    :return: None

    """

    try:
        if not on_progress:  # if progress function not specified, just run as is
            result = action(*args)

        else:
            # define progress function and put it on thread queue
            def progress(*a):
                thread_queue.put((on_progress, a + context))

            # retrieve the return value
            result = action(progress=progress, *args)

        if do_return:
            # put result into data queue to be read by main thread
            data_queue.put((result, context))

    except Exception as e:
        # If any exception occurs place appropriate callback to thread queue
        thread_queue.put((on_fail, (sys.exc_info(), ) + context))

    else:
        # no errors occurred, place exit callback onto thread queue
        thread_queue.put((on_exit, context))


def start_thread(action, args, context, on_exit, on_fail, on_progress=None, do_return=False):
    """Start a new thread and pass it the function to perform and appropriate callbacks associated with it.

    Interface between thread queue and the supplying controller. Controller shall supply the action to be performed
    appropriate callbacks based on the outcome of action (fail, success, progress) and arguments to action.

    :param action: function object, represent the function to be performed in a new thread
    :param args: tuple, arguments to action function
    :param context: string, some sort of identifier to keep track of what thread is launching the action and
    where it was launched from
    :param on_exit: function object, success callback
    :param on_fail: function object, fail callback
    :param on_progress: function object, update status of the threaded function callback
    :param do_return: boolean, flag whether to put the return value of action into the data queue or not
    :return: None

    """

    thread.start_new_thread(
        threaded, (action, args, context, on_exit, on_fail, on_progress, do_return)
    )


def debug_(*args, **kwargs):

    print "args are: " + str(args)
    print "kwargs are " + str(kwargs)

    demo()


def demo():
    """Demo the threading model.

    Taken from Programming Python by Mark Lutz.

    """
    import time

    if sys.version_info >= (3, 0):  # if python 3.x
        import tkinter

    else:  # if python 2.x
        import Tkinter as tkinter

    import ScrolledText

    def on_event(i):  # code that spawns thread
        my_name = 'thread-%s' % i
        start_thread(
            action=thread_action,
            args=(i, 3),
            context=(my_name,),
            on_exit=thread_exit,
            on_fail=thread_fail,
            on_progress=thread_progress,
        )

    # thread's main action
    def thread_action(id_, reps, progress):

        for i in range(reps):
            time.sleep(1)

            if progress:
                progress(i)  # progress callback: queued

        if (id_ % 2) == 1:
            raise Exception  # odd numbered: fail

    # thread exit/progress callbacks: dispatched off queue in main thread
    def thread_exit(my_name):
        text.insert('end', '%s\texit\n' % my_name)
        text.see('end')

    def thread_fail(exc_info, my_name):
        text.insert('end', '%s\tfail\t%s\n' % (my_name, exc_info[0]))
        text.see('end')

    def thread_progress(count, my_name):
        text.insert('end', '%s\tprog\t%s\n' % (my_name, count))
        text.see('end')
        text.update()  # works here: run in main thread

    # make enclosing GUI and start timer loop in main thread
    # spawn batch of worker threads on each mouse click: may overlap
    text = ScrolledText.ScrolledText()
    text.pack()
    thread_checker(text)  # start thread loop in main thread

    if sys.version_info >= (3, 0):  # if python 3.x
        text.bind('<Button-1>',  # 3.x need list for map, range ok
                  lambda event: list(map(on_event, range(6)))
        )
    else:
        text.bind('<Button-1>',
                  lambda event: map(on_event, range(6))
        )

    text.mainloop()  # pop-up window, enter tk event loop


if __name__ == "__main__":
    debug_(sys.argv)
