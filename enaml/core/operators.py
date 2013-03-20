#------------------------------------------------------------------------------
# Copyright (c) 2013, Nucleic Development Team.
#
# Distributed under the terms of the Modified BSD License.
#
# The full license is in the file COPYING.txt, distributed with this software.
#------------------------------------------------------------------------------
from contextlib import contextmanager

from atom.api import DefaultValue

from .dynamic_scope import DynamicScope, Nonlocals
from .exceptions import DeclarativeError
from .funchelper import call_func
from .standard_inverter import StandardInverter
from .standard_tracer import StandardTracer


class OperatorBase(object):
    """ The base class of the standard Enaml operator implementations.

    """
    __slots__ = 'binding'

    def __init__(self, binding):
        """ Initialize an OperatorBase.

        Parameters
        ----------
        binding : dict
            The dict created by the compiler which represents the
            operator binding.

        """
        self.binding = binding

    def get_locals(self, owner):
        """ Get the local scope for this operator and owner.

        Parameters
        ----------
        owner : Declarative
            The declarative object of interest.

        """
        scopename = self.binding['scopename']
        if scopename:
            return getattr(owner, scopename)
        return {}

    def release(self, owner):
        """ Release any resources held for the given owner.

        This method is called by a declarative object when it is being
        destroyed. It provides an opportunity for the operator to clean
        up any owner-specific state it may be holding. By default, this
        method is a no-op.

        Parameters
        ----------
        owner : Declarative
            The declarative object being destroyed.

        """
        pass


class OpSimple(OperatorBase):
    """ An operator class which implements the `=` operator semantics.

    """
    __slots__ = ()

    def eval(self, owner):
        """ Evaluate and return the expression value.

        This method is called by the '_run_eval_operator()' method on
        a Declarative instance.

        Parameters
        ----------
        owner : Declarative
            The declarative object requesting the evaluation.

        """
        overrides = {'nonlocals': Nonlocals(owner, None), 'self': owner}
        f_locals = self.get_locals(owner)
        func = self.binding['func']
        scope = DynamicScope(
            owner, f_locals, overrides, func.func_globals, None
        )
        return call_func(func, (), {}, scope)


class DeprecatedNotificationEvent(object):
    """ A backwards compatibility object.

    This object implements the magic 'event' scope object on the rhs
    of a '::' operator. That object is deprecated and this object will
    raise a deprecation warning when it is used.

    """
    # TODO: Remove this class in Enaml version 0.8.0
    __slots__ = ('_change', '_binding')

    _warning_registry = {}

    def __init__(self, change, binding):
        self._change = change
        self._binding = binding

    def _raise_warning(self):
        msg = "The 'event' scope object will be removed in Enaml "
        msg += "version 0.8.0. Use the 'change' scope object instead."
        binding = self._binding
        filename = binding['filename']
        lineno = binding['lineno']
        reg = self._warning_registry
        import warnings
        warnings.warn_explicit(msg, FutureWarning, filename, lineno, '', reg)

    @property
    def obj(self):
        self._raise_warning()
        return self._change['object']

    @property
    def name(self):
        self._raise_warning()
        return self._change['name']

    @property
    def old(self):
        self._raise_warning()
        change = self._change
        if change['type'] == 'event':
            from atom.catom import null
            return null
        return change['oldvalue']

    @property
    def new(self):
        self._raise_warning()
        change = self._change
        if change['type'] == 'event':
            return change['value']
        return change['newvalue']


class OpNotify(OperatorBase):
    """ An operator class which implements the `::` operator semantics.

    """
    __slots__ = ()

    def notify(self, change):
        """ Run the notification code bound to the operator.

        This method is called by the '_run_notify_operator()' method on
        a Declarative instance.

        Parameters
        ----------
        change : dict
            The change dict for the change on the requestor.

        """
        owner = change['object']
        nonlocals = Nonlocals(owner, None)
        overrides = {'change': change, 'nonlocals': nonlocals, 'self': owner}
        # TODO remove 'event' in Enaml version 0.8.0
        overrides['event'] = DeprecatedNotificationEvent(change, self.binding)
        f_locals = self.get_locals(owner)
        func = self.binding['func']
        scope = DynamicScope(
            owner, f_locals, overrides, func.func_globals, None
        )
        call_func(func, (), {}, scope)


class OpUpdate(OperatorBase):
    """ An operator class which implements the `>>` operator semantics.

    """
    __slots__ = ()

    def notify(self, change):
        """ Run the notification code bound to the operator.

        This method is called by the '_run_notify_operator()' method on
        a Declarative instance.

        Parameters
        ----------
        change : dict
            The change dict for the change on the requestor.

        """
        owner = change['object']
        nonlocals = Nonlocals(owner, None)
        overrides = {'nonlocals': nonlocals, 'self': owner}
        inverter = StandardInverter(nonlocals)
        f_locals = self.get_locals(owner)
        func = self.binding['func']
        scope = DynamicScope(
            owner, f_locals, overrides, func.func_globals, None
        )
        if change['type'] == 'event':
            value = change['value']
        else:
            value = change['newvalue']
        call_func(func, (inverter, value), {}, scope)


class SubscriptionObserver(object):
    """ An observer used to listen for changes in "<<" expressions.

    Instances of this class are created and managed by the OpSubscribe
    class when the operator is evaluated and traced.

    """
    __slots__ = ('owner', 'name')

    def __init__(self, owner, name):
        """ Initialize a SubscriptionObserver.

        Parameters
        ----------
        owner : Declarative
            The declarative owner of interest.

        name : str
            The name to which the operator is bound.

        """
        self.owner = owner  # will be reset to None by OpSubscribe
        self.name = name

    def __nonzero__(self):
        """ The notifier is valid when it has an internal owner.

        The atom observer mechanism will remove the observer when it
        tests boolean False. This removes the need to keep a weakref
        to the owner.

        """
        return self.owner is not None

    def __call__(self, change):
        """ The handler for the change notification.

        This will be invoked by the Atom observer mechanism when the
        item which is being observed changes.

        """
        owner = self.owner
        if owner is not None:
            name = self.name
            setattr(owner, name, owner._run_eval_operator(name))


class OpSubscribe(OperatorBase):
    """ An operator class which implements the `<<` operator semantics.

    """
    __slots__ = 'observers'

    def __init__(self, binding):
        """ Initialize a subscription operator.

        """
        super(OpSubscribe, self).__init__(binding)
        self.observers = {}

    def release(self, owner):
        """ Release the resources held for the given owner.

        """
        observer = self.observers.pop(owner, None)
        if observer is not None:
            observer.owner = None

    def eval(self, owner):
        """ Evaluate and return the expression value.

        """
        tracer = StandardTracer()
        overrides = {'nonlocals': Nonlocals(owner, tracer), 'self': owner}
        f_locals = self.get_locals(owner)
        func = self.binding['func']
        scope = DynamicScope(
            owner, f_locals, overrides, func.func_globals, tracer
        )
        result = call_func(func, (tracer,), {}, scope)

        # Invalidate the old notifier so that it gets cleaned up.
        observers = self.observers
        if owner in observers:
            observers[owner].owner = None

        # Create a new observer to bind to the current change set.
        observer = SubscriptionObserver(owner, self.binding['name'])
        observers[owner] = observer
        for obj, name in tracer.traced_items:
            obj.observe(name, observer)

        return result


# backwards compatibility traits support
import os
if os.environ.get('ENAML_TRAITS_SUPPORT'):

    from .traits_tracer import TraitsTracer

    class TraitsObserver(SubscriptionObserver):
        __slots__ = '__weakref__'
        def handler(self):
            owner = self.owner
            if owner is not None:
                name = self.name
                setattr(owner, name, owner._run_eval_operator(name))

    class OpSubscribe(OpSubscribe):
        __slots__ = ()
        def __init__(self, binding):
            OperatorBase.__init__(self, binding)
            self.observers = {}
        def release(self, owner):
            obs = self.observers.pop(owner, None)
            if obs is not None:
                atom_ob, traits_ob = obs
                atom_ob.owner = None
                traits_ob.owner = None
        def eval(self, owner):
            tracer = TraitsTracer()
            overrides = {'nonlocals': Nonlocals(owner, tracer), 'self': owner}
            f_locals = self.get_locals(owner)
            func = self.binding['func']
            scope = DynamicScope(
                owner, f_locals, overrides, func.func_globals, tracer
            )
            result = call_func(func, (tracer,), {}, scope)
            observers = self.observers
            if owner in observers:
                atom_ob, traits_ob = observers[owner]
                atom_ob.owner = None
                traits_ob.owner = None
            atom_ob = SubscriptionObserver(owner, self.binding['name'])
            traits_ob = TraitsObserver(owner, self.binding['name'])
            observers[owner] = (atom_ob, traits_ob)
            for obj, name in tracer.traced_items:
                obj.observe(name, atom_ob)
            for obj, name in tracer.traced_traits:
                obj.on_trait_change(traits_ob.handler, name)
            return result


class OpDelegate(OpSubscribe):
    """ An operator class which implements the `:=` operator semantics.

    """
    __slots__ = ()

    def notify(self, change):
        """ Run the notification code bound to the operator.

        This method is called by the '_run_notify_operator()' method on
        a Declarative instance.

        Parameters
        ----------
        change : dict
            The change dict for the change on the requestor.

        """
        owner = change['object']
        nonlocals = Nonlocals(owner, None)
        inverter = StandardInverter(nonlocals)
        overrides = {'nonlocals': nonlocals, 'self': owner}
        f_locals = self.get_locals(owner)
        func = self.binding['func2']
        scope = DynamicScope(
            owner, f_locals, overrides, func.func_globals, None
        )
        call_func(func, (inverter, change['newvalue']), {}, scope)


# TODO remove this in Enaml 0.8.0
_warn_color_registry = {}
def _warn_color_binding(name, binding):
    msg = "The '%s' attribute has been removed. Use '%s' instead. "
    msg += "Compatibility will be removed in Enaml version 0.8.0."
    d = {
        'fgcolor': ('fgcolor', 'foreground'),
        'bgcolor': ('bgcolor', 'background')
    }
    msg = msg % d[name]
    filename = binding['filename']
    lineno = binding['lineno']
    reg = _warn_color_registry
    import warnings
    warnings.warn_explicit(msg, FutureWarning, filename, lineno, '', reg)


def assert_d_member(klass, binding, readable, writable):
    """ Assert binding points to a valid declarative member.

    Parameters
    ----------
    klass : Declarative
        The declarative class which owns the binding.

    binding : dict
        The binding dict created by the enaml compiler.

    readable : bool
        Whether the member should have the 'd_readable' metadata flag.

    writable : bool
        Whether the member should have the 'd_writable' metadata flag.

    Returns
    -------
    result : tuple
        A 2-tuple of (name, member) on which the binding should operate.

    Raises
    ------
    DeclarativeError
        This will be raised if the member is not valid for the spec.

    """
    members = klass.members()
    name = binding['name']
    m = members.get(name)

    # TODO remove this backwards compatibility mod in Enaml 0.8.0
    if m is None:
        if name == 'fgcolor' and 'foreground' in members:
            _warn_color_binding(name, binding)
            name = binding['name'] = 'foreground'
            m = members.get(name)
        elif name == 'bgcolor' and 'background' in members:
            _warn_color_binding(name, binding)
            name = binding['name'] = 'background'
            m = members.get(name)

    if m is None or m.metadata is None or not m.metadata.get('d_member'):
        message = "'%s' is not a declarative member" % name
        raise DeclarativeError(message, binding)
    if readable and not m.metadata.get('d_readable'):
        message = "'%s' is not readable from enaml" % name
        raise DeclarativeError(message, binding)
    if writable and not m.metadata.get('d_writable'):
        message = "'%s' is not writable from enaml" % name
        raise DeclarativeError(message, binding)
    return (name, m)


def bind_read_operator(klass, binding, operator):
    """ Bind a readable operator for the binding to the given klass.

    Parameters
    ----------
    klass : Declarative
        The declarative class which owns the binding.

    binding : dict
        The binding dict created by the enaml compiler.

    operator : object
        The operator to bind to the class.

    """
    name, member = assert_d_member(klass, binding, True, False)
    klass._notify_operators().setdefault(name, []).append(operator)
    member.add_static_observer('_run_notify_operator')


def bind_write_operator(klass, binding, operator):
    """ Bind a writable operator for the binding to the given klass.

    Parameters
    ----------
    klass : Declarative
        The declarative class which owns the binding.

    binding : dict
        The binding dict created by the enaml compiler.

    operator : object
        The operator to bind to the class.

    """
    name, member = assert_d_member(klass, binding, False, True)
    klass._eval_operators()[name] = operator
    mode = (DefaultValue.ObjectMethod_Name, '_run_eval_operator')
    if member.default_value_mode != mode:
        clone = member.clone()
        clone.set_default_value_mode(*mode)
        klass.members()[name] = clone
        setattr(klass, name, clone)


def op_simple(klass, binding):
    """ The default Enaml operator function for the `=` operator.

    """
    bind_write_operator(klass, binding, OpSimple(binding))


def op_notify(klass, binding):
    """ The default Enaml operator function for the `::` operator.

    """
    bind_read_operator(klass, binding, OpNotify(binding))


def op_update(klass, binding):
    """ The default Enaml operator function for the `>>` operator.

    """
    bind_read_operator(klass, binding, OpUpdate(binding))


def op_subscribe(klass, binding):
    """ The default Enaml operator function for the `<<` operator.

    """
    bind_write_operator(klass, binding, OpSubscribe(binding))


def op_delegate(klass, binding):
    """ The default Enaml operator function for the `:=` operator.

    """
    operator = OpDelegate(binding)
    bind_read_operator(klass, binding, operator)
    bind_write_operator(klass, binding, operator)


DEFAULT_OPERATORS = {
    '=': op_simple,
    '::': op_notify,
    '>>': op_update,
    '<<': op_subscribe,
    ':=': op_delegate,
}


#: The internal stack of operators pushed by the operator context.
__operator_stack = []


@contextmanager
def operator_context(ops):
    """ Push operators onto the stack for the duration of the context.

    Parameters
    ----------
    ops : dict
        The dictionary of operators to push onto the stack.

    """
    __operator_stack.append(ops)
    print __operator_stack
    yield
    __operator_stack.pop()
    print __operator_stack


def __get_default_operators():
    """ Set the default operators.

    This function is for internal use only and may disappear at any time.

    """
    return DEFAULT_OPERATORS


def __set_default_operators(ops):
    """ Set the default operators.

    This function is for internal use only and may disappear at any time.

    """
    global DEFAULT_OPERATORS
    DEFAULT_OPERATORS = ops


def __get_operators():
    """ An internal routine used to get the operators for a given class.

    Operators resolution is performed in the following order:

        - The operators on the top of the operators stack.
        - The default operators via __get_default_operators()

    This function may disappear at any time.

    """
    if __operator_stack:
        return __operator_stack[-1]
    return __get_default_operators()
