/*
 * Node Graph: a GEGL graph as one filter, with every operation in it
 * checked first. A GEGL operation.
 *
 * node-graph.c
 * Copyright 2016 Øyvind Kolås <pippin@gimp.org> (gegl:gegl, which this
 *   follows)
 * Copyright 2026 David
 *
 * This library is free software; you can redistribute it and/or
 * modify it under the terms of the GNU Lesser General Public
 * License as published by the Free Software Foundation; either
 * version 3 of the License, or (at your option) any later version.
 *
 * This library is distributed in the hope that it will be useful,
 * but WITHOUT ANY WARRANTY; without even the implied warranty of
 * MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the GNU
 * Lesser General Public License for more details.
 *
 * You should have received a copy of the GNU Lesser General Public
 * License along with this library; if not, see
 * <https://www.gnu.org/licenses/>.
 *
 * Like gegl:gegl, it takes a graph in GEGL's chain syntax ("id=in
 * gaussian-blur std-dev-x=3 multiply aux=[ ref=in invert ]") and runs
 * it between its input and output. gegl:gegl may hold any operation,
 * including ones that write files, so GIMP refuses it as a layer filter
 * ("The filter is unsafe"): an XCF could otherwise overwrite files when
 * it is opened. This operation builds the graph in a scratch graph
 * first, never processed, and uses it only if no operation in it
 *
 *   - is a sink (writes somewhere: gegl:save, png-save, ff-save, ...);
 *   - is in the category input, output, programming, display, video or
 *     debug (loaders, savers, gegl:introspect, which runs a program);
 *   - has a file path or URI property;
 *   - is a graph of its own (gegl:gegl, nodes:graph), which would
 *     not be checked.
 *
 * Otherwise it shows the input unchanged and says why in "error".
 *
 * When the graph changes only in values (the same operations, linked
 * the same way), the values are set on the nodes already there, and only
 * the nodes whose values changed, and what follows them, are computed
 * again: every node in the graph keeps its result (cache policy
 * "always"), so what comes before is not. Otherwise the graph is built
 * anew.
 *
 * "layout" is not used here: the node editor of the GIMP plug-in keeps
 * the positions of its nodes in it, so that they are saved in the XCF
 * with the filter.
 */

#include <glib/gi18n-lib.h>

#ifdef GEGL_PROPERTIES

property_string (string, _("Graph"), "")
    description (_("The graph, in GEGL's chain syntax: "
                   "op [property=value]... with aux=[ ... ] for a second "
                   "input and id=name / ref=name to use a result twice"))
    ui_meta ("multiline", "true")

property_string (error, _("Error"), "")
    description (_("Why the graph is not used, or empty"))
    ui_meta ("error", "true")

property_string (layout, _("Layout"), "")
    description (_("Where the node editor shows the nodes (JSON); "
                   "not used for the image"))
    ui_meta ("visible", "0")

#else

#define GEGL_OP_META
#define GEGL_OP_NAME     node_graph
#define GEGL_OP_C_SOURCE node-graph.c

#include "gegl-op.h"
#include <string.h>

/* o->user_data */
typedef struct
{
  gchar  *string;  /* the graph that was built */
  GSList *stale;   /* nodes of the graph before, unlinked */
  GSList *nodes;   /* the nodes of the graph built, in GEGL's order */
} State;

static const gchar * const forbidden_categories[] =
{
  "input", "output", "programming", "display", "video", "debug"
};

static const gchar * const forbidden_names[] =
{
  "gegl:gegl", "nodes:graph",
  "gegl:introspect"   /* runs the program dot */
};

/* the reason an operation may not be in the graph, or NULL */
static gchar *
forbidden (GeglNode *node)
{
  GeglOperation *operation = gegl_node_get_gegl_operation (node);
  const gchar   *name      = gegl_node_get_operation (node);
  const gchar   *categories;
  GParamSpec   **pspecs;
  guint          n, i;
  gchar         *why = NULL;

  /* the proxies of a graph */
  if (operation == NULL || name == NULL)
    return NULL;

  for (i = 0; i < G_N_ELEMENTS (forbidden_names); i++)
    if (strcmp (name, forbidden_names[i]) == 0)
      return g_strdup_printf (_("%s is not allowed in the graph"), name);

  if (g_type_is_a (G_OBJECT_TYPE (operation), GEGL_TYPE_OPERATION_SINK))
    return g_strdup_printf (_("%s writes data out, not allowed"), name);

  categories = gegl_operation_get_key (name, "categories");
  if (categories)
    {
      gchar **cats = g_strsplit (categories, ":", 0);

      for (i = 0; cats[i] && ! why; i++)
        {
          guint j;

          for (j = 0; j < G_N_ELEMENTS (forbidden_categories); j++)
            if (strcmp (cats[i], forbidden_categories[j]) == 0)
              {
                why = g_strdup_printf (_("%s is in the category \"%s\", not allowed"),
                                       name, cats[i]);
                break;
              }
        }
      g_strfreev (cats);
      if (why)
        return why;
    }

  pspecs = g_object_class_list_properties (G_OBJECT_GET_CLASS (operation), &n);
  for (i = 0; i < n && ! why; i++)
    if (GEGL_IS_PARAM_SPEC_FILE_PATH (pspecs[i]) || GEGL_IS_PARAM_SPEC_URI (pspecs[i]))
      why = g_strdup_printf (_("%s reads or writes files (%s), not allowed"),
                             name, pspecs[i]->name);
  g_free (pspecs);

  return why;
}

/* the nodes of a graph, but its proxies */
static GSList *
graph_nodes (GeglNode *graph,
             GSList   *leave_out)
{
  GeglNode *input  = gegl_node_get_input_proxy (graph, "input");
  GeglNode *output = gegl_node_get_output_proxy (graph, "output");
  GSList   *children = gegl_node_get_children (graph);
  GSList   *nodes = NULL, *l;

  for (l = children; l; l = l->next)
    if (l->data != input && l->data != output && ! g_slist_find (leave_out, l->data))
      nodes = g_slist_prepend (nodes, g_object_ref (l->data));
  g_slist_free (children);
  return g_slist_reverse (nodes);
}

/* builds the graph in a scratch graph and checks every operation in it;
 * NULL if it may be used, otherwise why not. The scratch graph is put in
 * *scratch_out (when it may be used), never processed. */
static gchar *
check (const gchar  *string,
       gdouble       rel_dim,
       GeglNode    **scratch_out)
{
  GeglNode *scratch = gegl_node_new ();
  GeglNode *input   = gegl_node_get_input_proxy (scratch, "input");
  GeglNode *output  = gegl_node_get_output_proxy (scratch, "output");
  GError   *error   = NULL;
  gchar    *why     = NULL;
  GSList   *children, *l;

  gegl_node_link_many (input, output, NULL);
  gegl_create_chain (string, input, output, 0.0, rel_dim, NULL, &error);
  if (error)
    {
      why = g_strdup (error->message);
      g_clear_error (&error);
    }

  children = gegl_node_get_children (scratch);
  for (l = children; l && ! why; l = l->next)
    why = forbidden (GEGL_NODE (l->data));
  g_slist_free (children);

  if (why == NULL && scratch_out)
    *scratch_out = scratch;
  else
    g_object_unref (scratch);
  return why;
}

/* where a node gets its input from: the index of the producer in nodes,
 * -1 for the graph's input, -2 for nothing; and from which output pad */
static gint
producer_of (GeglNode     *node,
             const gchar  *pad,
             GSList       *nodes,
             GeglNode     *input,
             const gchar **out_pad)
{
  gchar    *producer_pad = NULL;
  GeglNode *producer = gegl_node_get_producer (node, (gchar *) pad, &producer_pad);
  gint      index;

  *out_pad = NULL;
  if (producer == NULL)
    return -2;
  *out_pad = g_intern_string (producer_pad);
  g_free (producer_pad);
  if (producer == input)
    return -1;
  index = g_slist_index (nodes, producer);
  return index < 0 ? -3 : index;
}

/* TRUE if the two graphs have the same operations, linked the same way */
static gboolean
same_shape (GeglNode *a_graph,
            GSList   *a,
            GeglNode *b_graph,
            GSList   *b)
{
  GeglNode    *a_in  = gegl_node_get_input_proxy (a_graph, "input");
  GeglNode    *b_in  = gegl_node_get_input_proxy (b_graph, "input");
  GeglNode    *a_out = gegl_node_get_output_proxy (a_graph, "output");
  GeglNode    *b_out = gegl_node_get_output_proxy (b_graph, "output");
  const gchar *a_pad, *b_pad;
  GSList      *la, *lb;

  if (g_slist_length (a) != g_slist_length (b))
    return FALSE;
  if (producer_of (a_out, "input", a, a_in, &a_pad) !=
      producer_of (b_out, "input", b, b_in, &b_pad) || a_pad != b_pad)
    return FALSE;

  for (la = a, lb = b; la && lb; la = la->next, lb = lb->next)
    {
      GeglNode *na = la->data, *nb = lb->data;
      gchar   **pads;
      gboolean  same = TRUE;
      gint      i;

      if (g_strcmp0 (gegl_node_get_operation (na), gegl_node_get_operation (nb)) != 0)
        return FALSE;
      pads = gegl_node_list_input_pads (na);
      for (i = 0; same && pads && pads[i]; i++)
        same = producer_of (na, pads[i], a, a_in, &a_pad) ==
               producer_of (nb, pads[i], b, b_in, &b_pad) && a_pad == b_pad;
      g_strfreev (pads);
      if (! same)
        return FALSE;
    }
  return TRUE;
}

static gboolean
same_value (const GValue *a,
            const GValue *b,
            GParamSpec   *pspec)
{
  if (G_VALUE_HOLDS (a, GEGL_TYPE_COLOR))
    {
      GeglColor *ca = g_value_get_object (a), *cb = g_value_get_object (b);
      gdouble    va[4], vb[4];

      if (ca == NULL || cb == NULL)
        return ca == cb;
      gegl_color_get_rgba (ca, &va[0], &va[1], &va[2], &va[3]);
      gegl_color_get_rgba (cb, &vb[0], &vb[1], &vb[2], &vb[3]);
      return memcmp (va, vb, sizeof va) == 0;
    }
  if (G_VALUE_HOLDS (a, G_TYPE_OBJECT) || G_VALUE_HOLDS (a, G_TYPE_POINTER))
    /* (curves, paths, buffers: not compared, set again) */
    return g_value_get_object (a) == g_value_get_object (b);
  return g_param_values_cmp (pspec, a, b) == 0;
}

/* sets the values of the nodes in from on the nodes in to, where they
 * differ; returns how many were set */
static gint
copy_values (GSList *from,
             GSList *to)
{
  gint n_set = 0;

  for (; from && to; from = from->next, to = to->next)
    {
      GeglNode    *src = from->data, *dst = to->data;
      GParamSpec **pspecs;
      guint        n, i;

      pspecs = gegl_operation_list_properties (gegl_node_get_operation (src), &n);
      for (i = 0; i < n; i++)
        {
          GValue a = G_VALUE_INIT, b = G_VALUE_INIT;

          if ((pspecs[i]->flags & G_PARAM_READWRITE) != G_PARAM_READWRITE ||
              (pspecs[i]->flags & G_PARAM_CONSTRUCT_ONLY))
            continue;
          g_value_init (&a, pspecs[i]->value_type);
          g_value_init (&b, pspecs[i]->value_type);
          gegl_node_get_property (src, pspecs[i]->name, &a);
          gegl_node_get_property (dst, pspecs[i]->name, &b);
          if (! same_value (&a, &b, pspecs[i]))
            {
              gegl_node_set_property (dst, pspecs[i]->name, &a);
              n_set++;
            }
          g_value_unset (&a);
          g_value_unset (&b);
        }
      g_free (pspecs);
    }
  return n_set;
}

/* unlinks the nodes of the graph (but the proxies) and keeps them in
 * state->stale. They are not removed here: GEGL may be preparing them
 * right now, as this runs from prepare. The ones from the time before
 * are removed, nothing links to them any more. */
static void
unlink_graph (GeglNode *gegl,
              State    *state)
{
  GeglNode *input  = gegl_node_get_input_proxy (gegl, "input");
  GeglNode *output = gegl_node_get_output_proxy (gegl, "output");
  GSList   *children, *l;

  for (l = state->stale; l; l = l->next)
    gegl_node_remove_child (gegl, GEGL_NODE (l->data));
  g_slist_free_full (state->stale, g_object_unref);
  state->stale = NULL;

  gegl_node_link_many (input, output, NULL);
  g_slist_free_full (state->nodes, g_object_unref);
  state->nodes = NULL;

  children = gegl_node_get_children (gegl);
  for (l = children; l; l = l->next)
    {
      GeglNode *child = GEGL_NODE (l->data);
      gchar   **pads;
      gint      i;

      if (child == input || child == output)
        continue;
      pads = gegl_node_list_input_pads (child);
      for (i = 0; pads && pads[i]; i++)
        gegl_node_disconnect (child, pads[i]);
      g_strfreev (pads);
      state->stale = g_slist_prepend (state->stale, g_object_ref (child));
    }
  g_slist_free (children);
}

static void
prepare (GeglOperation *operation)
{
  GeglProperties *o    = GEGL_PROPERTIES (operation);
  GeglNode       *gegl = operation->node;
  GeglNode       *input, *output;
  GeglNode       *scratch = NULL;
  State          *state;
  gdouble         rel_dim;
  gchar          *why;

  if (o->user_data == NULL)
    o->user_data = g_new0 (State, 1);
  state = o->user_data;

  /* only when the graph changed */
  if (state->string && g_str_equal (state->string, o->string))
    return;
  g_free (state->string);
  state->string = g_strdup (o->string);

  input  = gegl_node_get_input_proxy (gegl, "input");
  output = gegl_node_get_output_proxy (gegl, "output");
  rel_dim = gegl_node_get_bounding_box (input).height;

  why = check (o->string, rel_dim, &scratch);
  if (why == NULL)
    {
      GSList *wanted = graph_nodes (scratch, NULL);

      if (state->nodes && same_shape (scratch, wanted, gegl, state->nodes))
        {
          /* the same graph but for values: set those */
          copy_values (wanted, state->nodes);
        }
      else
        {
          GError *error = NULL;

          unlink_graph (gegl, state);
          gegl_create_chain (o->string, input, output, 0.0, rel_dim, NULL, &error);
          if (error)
            {
              why = g_strdup (error->message);
              g_clear_error (&error);
              /* nothing half built: the input as it is */
              unlink_graph (gegl, state);
            }
          else
            {
              GSList *l;

              state->nodes = graph_nodes (gegl, state->stale);
              /* each node keeps its result: GEGL by itself does so only
               * for some operations, and a value changed late in the
               * graph would otherwise compute all of it again */
              for (l = state->nodes; l; l = l->next)
                g_object_set (l->data, "cache-policy", GEGL_CACHE_POLICY_ALWAYS, NULL);
            }
        }
      g_slist_free_full (wanted, g_object_unref);
      g_object_unref (scratch);
    }
  else
    unlink_graph (gegl, state);

  /* (set only when it changes: setting it notifies) */
  if (g_strcmp0 (o->error, why ? why : "") != 0)
    g_object_set (operation, "error", why ? why : "", NULL);
  g_free (why);
}

static void
attach (GeglOperation *operation)
{
  GeglNode *gegl   = operation->node;
  GeglNode *input  = gegl_node_get_input_proxy (gegl, "input");
  GeglNode *output = gegl_node_get_output_proxy (gegl, "output");

  gegl_node_link_many (input, output, NULL);
  prepare (operation);
}

static void
dispose (GObject *object)
{
  GeglProperties *o     = GEGL_PROPERTIES (object);
  State          *state = o->user_data;

  if (state)
    {
      g_free (state->string);
      g_slist_free_full (state->stale, g_object_unref);
      g_slist_free_full (state->nodes, g_object_unref);
      g_free (state);
      o->user_data = NULL;
    }
  G_OBJECT_CLASS (gegl_op_parent_class)->dispose (object);
}

static void
gegl_op_class_init (GeglOpClass *klass)
{
  GObjectClass       *object_class    = G_OBJECT_CLASS (klass);
  GeglOperationClass *operation_class = GEGL_OPERATION_CLASS (klass);

  object_class->dispose    = dispose;
  operation_class->attach  = attach;
  operation_class->prepare = prepare;

  gegl_operation_class_set_keys (operation_class,
    "name",        "nodes:graph",
    "title",       _("Node Graph"),
    "categories",  "generic",
    "description", _("A graph of GEGL operations as one filter, made with "
                     "the node editor. Operations that read or write files "
                     "are not allowed in it."),
    NULL);
}

#endif
