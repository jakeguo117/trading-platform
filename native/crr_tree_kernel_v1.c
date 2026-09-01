#define PY_SSIZE_T_CLEAN
#include <Python.h>

#include <fenv.h>
#include <float.h>
#include <math.h>
#include <stdint.h>

#ifndef GLD_SOURCE_MANIFEST_SHA256
#error "GLD_SOURCE_MANIFEST_SHA256 must be supplied by the offline builder"
#endif
#ifndef GLD_BUILD_INPUT_SHA256
#error "GLD_BUILD_INPUT_SHA256 must be supplied by the offline builder"
#endif
#ifndef GLD_COMPILER_FLAGS_SHA256
#error "GLD_COMPILER_FLAGS_SHA256 must be supplied by the offline builder"
#endif
#ifndef GLD_DEPENDENCY_MANIFEST_SHA256
#error "GLD_DEPENDENCY_MANIFEST_SHA256 must be supplied by the offline builder"
#endif

#define GLD_CRR_ABI_ID "GLD_CRR_TREE_KERNEL_ABI_V1"
#define GLD_CRR_ABI_VERSION 1
#define GLD_CRR_MAX_STEPS 1025

enum gld_crr_status_v1 {
    GLD_CRR_OK = 0,
    GLD_CRR_TREE_NUMERIC_INVALID = 1,
    GLD_CRR_TREE_PROBABILITY_INVALID = 2,
    GLD_CRR_DELTA_OUT_OF_RANGE = 3
};

struct gld_crr_tree_output_v1 {
    double price;
    double delta;
    uint64_t early_exercise_nodes;
};

static int
gld_maximum_is_finite(double first, double second, double third, double fourth,
                      double fifth, double sixth, double seventh)
{
    return isfinite(first) && isfinite(second) && isfinite(third) &&
           isfinite(fourth) && isfinite(fifth) && isfinite(sixth) &&
           isfinite(seventh);
}

static enum gld_crr_status_v1
gld_crr_tree_eval_v1(double spot, double strike, double years, double rate,
                     double effective_yield, double volatility, int steps,
                     struct gld_crr_tree_output_v1 *output)
{
    double values[GLD_CRR_MAX_STEPS + 1];
    double dt;
    double log_up;
    double up;
    double down;
    double denominator;
    double growth;
    double discount;
    double probability;
    double log_spot;
    double base_asset;
    double asset_ratio;
    double asset;
    double intrinsic;
    double down_weight;
    double t1_down = NAN;
    double t1_up = NAN;
    double continuation;
    double materiality;
    double maximum;
    double candidate;
    double root_denominator;
    double delta;
    double price;
    uint64_t early_exercise_nodes = 0;
    int node;
    int level;

    if (sizeof(double) != 8 || FLT_RADIX != 2 || DBL_MANT_DIG != 53 ||
        FLT_EVAL_METHOD != 0 || fegetround() != FE_TONEAREST) {
        return GLD_CRR_TREE_NUMERIC_INVALID;
    }
    if (!isfinite(spot) || !isfinite(strike) || !isfinite(years) ||
        !isfinite(rate) || !isfinite(effective_yield) ||
        !isfinite(volatility) || spot <= 0.0 || strike <= 0.0 || years <= 0.0 ||
        volatility <= 0.0 || steps < 2 || steps > GLD_CRR_MAX_STEPS) {
        return GLD_CRR_TREE_NUMERIC_INVALID;
    }

    dt = years / (double)steps;
    log_up = volatility * sqrt(dt);
    up = exp(log_up);
    down = 1.0 / up;
    denominator = up - down;
    growth = exp((rate - effective_yield) * dt);
    discount = exp(-rate * dt);
    if (!gld_maximum_is_finite(dt, log_up, up, down, denominator, growth,
                               discount) ||
        denominator <= 0.0 || discount <= 0.0) {
        return GLD_CRR_TREE_NUMERIC_INVALID;
    }

    probability = (growth - down) / denominator;
    if (!isfinite(probability) || probability < 0.0 || probability > 1.0) {
        return GLD_CRR_TREE_PROBABILITY_INVALID;
    }

    log_spot = log(spot);
    if (log_spot + (double)steps * log_up >= log(DBL_MAX)) {
        return GLD_CRR_TREE_NUMERIC_INVALID;
    }
    base_asset = spot * exp(-(double)steps * log_up);
    if (!isfinite(base_asset)) {
        return GLD_CRR_TREE_NUMERIC_INVALID;
    }
    asset_ratio = up * up;
    asset = base_asset;
    for (node = 0; node <= steps; ++node) {
        intrinsic = asset - strike;
        values[node] = intrinsic > 0.0 ? intrinsic : 0.0;
        asset *= asset_ratio;
    }

    down_weight = 1.0 - probability;
    for (level = steps - 1; level >= 0; --level) {
        base_asset *= up;
        asset = base_asset;
        for (node = 0; node <= level; ++node) {
            continuation = discount *
                           (down_weight * values[node] +
                            probability * values[node + 1]);
            intrinsic = asset - strike;
            if (intrinsic > continuation) {
                values[node] = intrinsic;
                maximum = 1.0;
                candidate = fabs(intrinsic);
                if (candidate > maximum) {
                    maximum = candidate;
                }
                candidate = fabs(continuation);
                if (candidate > maximum) {
                    maximum = candidate;
                }
                materiality = 1e-12 * maximum;
                if (intrinsic > 0.0 &&
                    intrinsic - continuation > materiality) {
                    ++early_exercise_nodes;
                }
            }
            else {
                values[node] = continuation;
            }
            asset *= asset_ratio;
        }
        if (level == 1) {
            t1_down = values[0];
            t1_up = values[1];
        }
    }

    root_denominator = spot * denominator;
    if (root_denominator <= 0.0) {
        return GLD_CRR_TREE_NUMERIC_INVALID;
    }
    delta = (t1_up - t1_down) / root_denominator;
    price = values[0];
    if (!isfinite(price) || !isfinite(delta) || price < 0.0) {
        return GLD_CRR_TREE_NUMERIC_INVALID;
    }
    if (delta < -1e-12 || delta > 1.0 + 1e-12) {
        return GLD_CRR_DELTA_OUT_OF_RANGE;
    }
    if (delta < 0.0) {
        delta = 0.0;
    }
    else if (delta > 1.0) {
        delta = 1.0;
    }

    output->price = price;
    output->delta = delta;
    output->early_exercise_nodes = early_exercise_nodes;
    return GLD_CRR_OK;
}

static PyObject *
gld_failure_tuple(enum gld_crr_status_v1 status)
{
    PyObject *result = PyTuple_New(4);
    if (result == NULL) {
        return NULL;
    }
    PyTuple_SET_ITEM(result, 0, PyLong_FromLong((long)status));
    Py_INCREF(Py_None);
    PyTuple_SET_ITEM(result, 1, Py_None);
    Py_INCREF(Py_None);
    PyTuple_SET_ITEM(result, 2, Py_None);
    PyTuple_SET_ITEM(result, 3, PyLong_FromLong(0));
    if (PyErr_Occurred()) {
        Py_DECREF(result);
        return NULL;
    }
    return result;
}

static PyObject *
gld_identity_v1(PyObject *self, PyObject *Py_UNUSED(ignored))
{
    (void)self;
    if (sizeof(double) != 8 || FLT_RADIX != 2 || DBL_MANT_DIG != 53 ||
        FLT_EVAL_METHOD != 0 || fegetround() != FE_TONEAREST) {
        PyErr_SetString(PyExc_RuntimeError,
                        "native floating-point environment mismatch");
        return NULL;
    }
    return Py_BuildValue("(sissss)", GLD_CRR_ABI_ID, GLD_CRR_ABI_VERSION,
                         GLD_SOURCE_MANIFEST_SHA256, GLD_BUILD_INPUT_SHA256,
                         GLD_COMPILER_FLAGS_SHA256,
                         GLD_DEPENDENCY_MANIFEST_SHA256);
}

static PyObject *
gld_tree_eval_v1(PyObject *self, PyObject *args)
{
    double spot;
    double strike;
    double years;
    double rate;
    double effective_yield;
    double volatility;
    long steps_long;
    int steps;
    enum gld_crr_status_v1 status;
    struct gld_crr_tree_output_v1 output = {0.0, 0.0, 0};
    PyObject *result;

    (void)self;
    if (!PyTuple_CheckExact(args) || PyTuple_GET_SIZE(args) != 7) {
        PyErr_SetString(PyExc_TypeError,
                        "tree_eval_v1 requires seven positional arguments");
        return NULL;
    }
    if (!PyFloat_CheckExact(PyTuple_GET_ITEM(args, 0)) ||
        !PyFloat_CheckExact(PyTuple_GET_ITEM(args, 1)) ||
        !PyFloat_CheckExact(PyTuple_GET_ITEM(args, 2)) ||
        !PyFloat_CheckExact(PyTuple_GET_ITEM(args, 3)) ||
        !PyFloat_CheckExact(PyTuple_GET_ITEM(args, 4)) ||
        !PyFloat_CheckExact(PyTuple_GET_ITEM(args, 5)) ||
        !PyLong_CheckExact(PyTuple_GET_ITEM(args, 6))) {
        PyErr_SetString(PyExc_TypeError,
                        "tree_eval_v1 requires six exact floats and one exact int");
        return NULL;
    }

    spot = PyFloat_AS_DOUBLE(PyTuple_GET_ITEM(args, 0));
    strike = PyFloat_AS_DOUBLE(PyTuple_GET_ITEM(args, 1));
    years = PyFloat_AS_DOUBLE(PyTuple_GET_ITEM(args, 2));
    rate = PyFloat_AS_DOUBLE(PyTuple_GET_ITEM(args, 3));
    effective_yield = PyFloat_AS_DOUBLE(PyTuple_GET_ITEM(args, 4));
    volatility = PyFloat_AS_DOUBLE(PyTuple_GET_ITEM(args, 5));
    steps_long = PyLong_AsLong(PyTuple_GET_ITEM(args, 6));
    if (steps_long == -1 && PyErr_Occurred()) {
        PyErr_Clear();
        return gld_failure_tuple(GLD_CRR_TREE_NUMERIC_INVALID);
    }
    if (steps_long < 2 || steps_long > GLD_CRR_MAX_STEPS) {
        return gld_failure_tuple(GLD_CRR_TREE_NUMERIC_INVALID);
    }
    steps = (int)steps_long;

    Py_BEGIN_ALLOW_THREADS
    status = gld_crr_tree_eval_v1(spot, strike, years, rate, effective_yield,
                                  volatility, steps, &output);
    Py_END_ALLOW_THREADS

    if (status != GLD_CRR_OK) {
        return gld_failure_tuple(status);
    }
    result = PyTuple_New(4);
    if (result == NULL) {
        return NULL;
    }
    PyTuple_SET_ITEM(result, 0, PyLong_FromLong(0));
    PyTuple_SET_ITEM(result, 1, PyFloat_FromDouble(output.price));
    PyTuple_SET_ITEM(result, 2, PyFloat_FromDouble(output.delta));
    PyTuple_SET_ITEM(result, 3,
                     PyLong_FromUnsignedLongLong(output.early_exercise_nodes));
    if (PyErr_Occurred()) {
        Py_DECREF(result);
        return NULL;
    }
    return result;
}

static PyMethodDef gld_crr_methods[] = {
    {"identity_v1", gld_identity_v1, METH_NOARGS,
     "Return the immutable native backend identity."},
    {"tree_eval_v1", gld_tree_eval_v1, METH_VARARGS,
     "Evaluate one positional American-call CRR tree."},
    {NULL, NULL, 0, NULL}
};

static struct PyModuleDef gld_crr_module = {
    PyModuleDef_HEAD_INIT,
    "_crr_tree_kernel",
    "Deterministic GLD American-call CRR tree kernel.",
    -1,
    gld_crr_methods,
    NULL,
    NULL,
    NULL,
    NULL
};

PyMODINIT_FUNC
PyInit__crr_tree_kernel(void)
{
    return PyModule_Create(&gld_crr_module);
}
