import io
import json
import re
import threading
from time import sleep
from typing import Dict, List
import zipfile
from cirro import DataPortal
from cirro.sdk.reference import DataPortalReference
from cirro.sdk.process import DataPortalProcess
from cirro.sdk.dataset import DataPortalDataset
from cirro.config import AppConfig, list_tenants
from cirro.auth.device_code import DeviceCodeAuth
from cirro import CirroApi
from gql.transport.requests import TransportAlreadyConnected
import streamlit as st
from streamlit.delta_generator import DeltaGenerator
from streamlit.runtime.scriptrunner import get_script_run_ctx
from streamlit.runtime.scriptrunner import script_run_context


def get_portal():

    # If login information has not been setup, skip this
    if st.session_state.get("DataPortal") is None:
        cirro_login(login_empty, base_url)

    msg = "Error encountered logging in"
    assert st.session_state.get("DataPortal") is not None, msg

    return st.session_state["DataPortal"]


def session_cache(func):
    def inner(*args, **kwargs):

        # If login information has not been setup, skip this
        if st.session_state.get("DataPortal") is None:
            cirro_login(login_empty, base_url)

        # Get the session context, which has a unique ID element
        ctx = get_script_run_ctx()

        # Define a cache key based on the function name and arguments
        cache_key = ".".join([
            str(ctx.session_id),
            func.__name__,
            ".".join(map(str, args)),
            ".".join([
                f"{k}={v}"
                for k, v in kwargs.items()
            ])
        ])

        # If the value has not been computed
        if st.session_state.get(cache_key) is None:
            # Compute it
            st.session_state[cache_key] = func(
                *args,
                **kwargs
            )

        # Return that value
        return st.session_state[cache_key]

    return inner


def autoretry(func, retries=15, exception=TransportAlreadyConnected):
    def inner(*args, **kwargs):

        result = None
        for i in range(retries):
            try:
                result = func(*args, **kwargs)
            except exception as e:
                if i == (retries - 1):
                    raise e
                else:
                    sleep(0.1)
            if result is not None:
                break
        return result

    return inner


def cirro_login(base_url: str, login_empty: DeltaGenerator):
    # If we have not logged in yet
    if st.session_state.get('DataPortal') is None:

        # Connect to Cirro - capturing the login URL
        auth_io = io.StringIO()
        cirro_login_thread = threading.Thread(
            target=cirro_login_sub,
            args=(auth_io, base_url)
        )
        script_run_context.add_script_run_ctx(cirro_login_thread)

        cirro_login_thread.start()

        login_string = auth_io.getvalue()

        while len(login_string) == 0 and cirro_login_thread.is_alive():
            sleep(1)
            login_string = auth_io.getvalue()

        login_empty.write(login_string)
        cirro_login_thread.join()
        login_empty.empty()


def cirro_login_sub(auth_io: io.StringIO, base_url: str):

    app_config = AppConfig(base_url=base_url)

    st.session_state['DataPortal-auth_info'] = DeviceCodeAuth(
        region=app_config.region,
        client_id=app_config.client_id,
        auth_endpoint=app_config.auth_endpoint,
        enable_cache=False,
        auth_io=auth_io
    )

    st.session_state['DataPortal-client'] = CirroApi(
        auth_info=st.session_state['DataPortal-auth_info'],
        base_url=base_url
    )
    st.session_state['DataPortal'] = DataPortal(
        client=st.session_state['DataPortal-client']
    )


@autoretry
@session_cache
def list_datasets_in_project(project_name) -> List[str]:

    # Connect to Cirro
    portal: DataPortal = get_portal()

    # Access the project
    project = portal.get_project_by_name(project_name)

    # Get the list of datasets available (using their easily-readable names)
    try:
        return [""] + [ds.name for ds in project.list_datasets()]
    except TypeError:
        return [""]


@autoretry
@session_cache
def list_processes(ingest=False) -> List[str]:

    # Connect to Cirro
    portal: DataPortal = get_portal()

    # List the projects available
    process_list: List[DataPortalProcess] = portal.list_processes(
        ingest=ingest
    )
    if ingest:
        process_list = process_list + portal.list_processes()

    # Return the list of processes available
    # (using their easily-readable names)
    process_list = list(set([
        f"{process.name} ({process.id})"
        for process in process_list
    ]))
    process_list.sort()
    return process_list


@autoretry
@session_cache
def list_projects() -> List[str]:

    # Connect to Cirro
    portal: DataPortal = get_portal()

    # List the projects available
    project_list = portal.list_projects()

    # Return the list of projects available (using their easily-readable names)
    project_list = [proj.name for proj in project_list]
    project_list.sort()
    return project_list


@autoretry
@session_cache
def list_references() -> List[DataPortalReference]:

    # Connect to Cirro
    portal: DataPortal = get_portal()

    # List the references available
    reference_list: List[DataPortalReference] = portal.list_reference_types()

    # {
    #   'name': 'Barcode files (general)',
    #   'description': 'List of line-separated barcodes',
    #   'directory': 'barcodes',
    #   'validation': [{'fileType': 'txt', 'saveAs': 'barcode.txt'}]
    # }
    return reference_list


@autoretry
@session_cache
def get_reference_str(ref_name) -> str:

    # Connect to Cirro
    portal: DataPortal = get_portal()

    for ref in portal.list_reference_types():
        if ref.name == ref_name:
            if "validation" in ref.__dict__:
                filename = ref.validation[0]['saveAs']
            else:
                filename = "*"
            return f"**/{ref.directory}/**/{filename}"


@autoretry
@session_cache
def get_dataset(project_name, dataset_name) -> DataPortalDataset:
    """Return a Cirro Dataset object."""

    # Connect to Cirro
    portal: DataPortal = get_portal()

    # Access the project
    project = portal.get_project_by_name(project_name)

    # Get the dataset
    return project.get_dataset_by_name(dataset_name)


@session_cache
def list_files_in_dataset(project_name, dataset_name):
    """Return a list of files in a dataset."""

    return [
        f.name
        for f in get_dataset(project_name, dataset_name).list_files()
    ]


@session_cache
def read_csv(project_name, dataset_name, fn, **kwargs):
    """Read a CSV from a dataset in Cirro."""

    # print(f"Reading {fn} from {project_name} / {dataset_name}")
    return (
        get_dataset(project_name, dataset_name)
        .list_files()
        .get_by_name(f"data/{fn}")
        .read_csv(**kwargs)
    )


class WorkflowConfigElement:
    """Parent class for workflow configuration elements."""

    workflow_config: 'WorkflowConfig'

    def __init__(self, workflow_config: 'WorkflowConfig'):
        self.workflow_config = workflow_config

    def load(self, config: dict) -> None:
        """
        Set up attributes based on the contents
        of the configuration JSON
        """
        pass

    def dump(self, config: dict) -> None:
        """
        The attributes of the configuration will be
        populated based on the state of this element.
        """
        pass

    def serve(self, config: 'WorkflowConfig') -> None:
        """
        Serve the user interaction for modifying the element
        """
        pass


class SourceConfig(WorkflowConfigElement):

    root_kwargs: dict
    code_kwargs: dict

    def __init__(self, workflow_config: 'WorkflowConfig'):
        self.workflow_config = workflow_config
        self._id = "dynamo"
        self.root_kwargs = {
            "id": "unique-workflow-id",
            "name": "My Workflow Name",
            "desc": "Description of my workflow",
            "executor": "NEXTFLOW",
            "documentationUrl": "",
            "childProcessIds": [],
            "parentProcessIds": []
        }
        self.code_kwargs = {
            "repository": "GITHUBPUBLIC",
            "script": "main.nf",
            "uri": "organization/repository_name",
            "version": "main"
        }

    def load(self, config: dict) -> None:

        for kw, default in self.root_kwargs.items():
            self.__dict__[kw] = (
                config
                .get(self._id, dict())
                .get(kw, default)
            )

        for kw, default in self.code_kwargs.items():
            self.__dict__[kw] = (
                config
                .get(self._id, dict())
                .get("code", dict())
                .get(kw, default)
            )

    def dump(self, config: dict) -> None:
        """
        The attributes of the configuration will be
        populated based on the state of this element.
        """

        for kw in self.root_kwargs.keys():
            val = self.__dict__.get(kw)
            config[self._id][kw] = val.upper() if kw == "executor" else val

        for kw in self.code_kwargs.keys():
            config[self._id]["code"][kw] = self.__dict__.get(kw)

    def update_value(self, config: 'WorkflowConfig', kw: str):

        # Get the updated value
        key = f"{self._id}.{kw}.{config.form_ix}"
        if key not in st.session_state:
            return
        val = st.session_state[key]

        # If no change has been made
        if self.__dict__[kw] == val:
            # Take no action
            return

        # Otherwise, make the change and redraw the form
        self.__dict__[kw] = val
        config.save_config()
        config.reset()

    def input_kwargs(self, config: 'WorkflowConfig', kw: str):
        return dict(
            key=f"{self._id}.{kw}.{config.form_ix}",
            on_change=self.update_value,
            args=(config, kw)
        )

    def input_process_kwargs(self, config: 'WorkflowConfig', kw: str):
        return dict(
            key=f"{self._id}.{kw}.{config.form_ix}",
            on_change=self.update_process_list,
            args=(config, kw)
        )

    def get_process_id(self, long_name: str):
        return long_name.rsplit(" (", 1)[-1].rstrip(")")

    def update_process_list(self, config: 'WorkflowConfig', kw: str):
        key = f"{self._id}.{kw}.{config.form_ix}"
        if key not in st.session_state:
            return
        process_list = st.session_state[key]

        # Get the process IDs for each process
        process_list = [
            self.get_process_id(process)
            for process in process_list
        ]

        if self.__dict__[kw] == process_list:
            return

        self.__dict__[kw] = process_list
        config.save_config()
        config.reset()

    def id_is_unique(self) -> bool:
        for process in list_processes(ingest=True):
            if f"({self.id})" in process:
                return False
        return True

    def serve(self, config: 'WorkflowConfig') -> None:
        """
        Serve the user interaction for modifying the element
        """

        config.form_container.text_input(
            "Workflow ID",
            self.id,
            help="Must be all lowercase alphanumeric with dashes",
            **self.input_kwargs(config, "id")
        )

        if not self.id_is_unique():
            config.form_container.write(
                "Workflow ID is currently used in Cirro"
            )

        config.form_container.text_input(
            "Workflow Name",
            value=self.name,
            help="Short name used to display the workflow in a list",
            **self.input_kwargs(config, "name")
        )

        config.form_container.text_input(
            "Workflow Description",
            value=self.desc,
            help="Longer description providing more details on the workflow (8-15 words)",  # noqa
            **self.input_kwargs(config, "desc")
        )

        config.form_container.radio(
            "Workflow Executor",
            ["Nextflow", "Cromwell"],
            ["Nextflow", "Cromwell"].index(self.executor.title()),
            **self.input_kwargs(config, "executor")
        ).upper()

        config.form_container.text_input(
            "Workflow Repository Path",
            help="Path to workflow's GitHub repository formatted as organization/repository",
            value=self.uri,
            **self.input_kwargs(config, "uri")
        )

        config.form_container.text_input(
            "Workflow Entrypoint Script",
            value=self.script,
            help="Script from the repository used to launch the workflow",
            **self.input_kwargs(config, "script")
        )

        config.form_container.text_input(
            "Repository Branch / Version",
            value=self.version,
            help="Often 'main' (branch), but also supports commits, tags, and releases.",
            **self.input_kwargs(config, "version")
        )

        config.form_container.selectbox(
            "Public or Private GitHub Repository",
            ["GITHUBPUBLIC", "GITHUBPRIVATE"],
            ["GITHUBPUBLIC", "GITHUBPRIVATE"].index(self.repository),
            format_func=lambda x: x.replace('GITHUB', '').title(),
            help="Supports branch names, commits, tags, and releases.",
            **self.input_kwargs(config, "repository")
        )
        if self.repository == "GITHUBPRIVATE":
            config.form_container.write("""
Make sure to connect your private GitHub repository to Cirro by installing
the [Cirro Data Portal App](https://github.com/apps/cirro-data-portal).
""")

        config.form_container.multiselect(
            "Processes with outputs that can be used as inputs to this workflow",
            list_processes(ingest=True),
            [
                process for process in list_processes(ingest=True)
                if self.get_process_id(process) in self.parentProcessIds
            ],
            help="Select processes which produce outputs that can be used as inputs when running this workflow",  # noqa
            **self.input_process_kwargs(config, "parentProcessIds")
        )

        config.form_container.multiselect(
            "Processes that can use this workflow's outputs as inputs",
            list_processes(),
            [
                process for process in list_processes()
                if self.get_process_id(process) in self.childProcessIds
            ],
            help="Select processes which can be run on the output datasets produced by this workflow",  # noqa
            **self.input_process_kwargs(config, "childProcessIds")
        )


class UIElement:
    """Helper class with useful interface elements."""

    id: str
    ui_key_prefix: str
    expander: DeltaGenerator
    workflow_config: 'WorkflowConfig'

    def ui_key(self, kw: str):
        return f"{self.ui_key_prefix}.{self.id}.{kw}.{self.workflow_config.form_ix}"  # noqa

    def remove(self):
        """Remove this param from the inputs."""

        self.deleted = True
        self.workflow_config.save_config()
        self.workflow_config.reset()

    def text_input(self, kw, title, value, **kwargs):

        self.expander.text_input(
            title,
            value,
            **self.input_kwargs(kw),
            **kwargs
        )

    def number_input(self, kw, title, value, **kwargs):

        self.expander.number_input(
            title,
            value,
            **self.input_kwargs(kw),
            **kwargs
        )

    def integer_input(self, kw, title, value, **kwargs):

        try:
            value = int(value)
        except ValueError:
            value = 0

        self.expander.number_input(
            title,
            value,
            step=1,
            **self.input_kwargs(kw),
            **kwargs
        )

    def dropdown(self, kw, title, options, index, **kwargs):

        self.expander.selectbox(
            title,
            options,
            index=index,
            **self.input_kwargs(kw),
            **kwargs
        )

    def input_kwargs(self, kw):
        return dict(
            key=self.ui_key(kw),
            on_change=self.update_attribute,
            args=(kw,)
        )


class Param(UIElement):

    ui_key_prefix = "params"
    workflow_config: 'WorkflowConfig'
    input_type: str
    input_type_options = [
        "Dataset Name",
        "Form Entry",
        "Hardcoded Value",
        "Input Directory",
        "Output Directory"
    ]

    input_type_descriptions = {
        "Dataset Name": "The parameter will be populated with the name of the new dataset which was provided by the user.",
        "Form Entry": "The parameter will be set by the user using a form.",
        "Hardcoded Value": "The parameter will be a hard-coded value.",
        "Input Directory": "The parameter will be populated with the base URL of the files which make up the contents of the input dataset.",
        "Output Directory": "The parameter will be populated with the base URL of the dataset which will be created with the outputs of this workflow."
    }

    input_type_values = {
        "Output Directory": "$.params.dataset.s3|/data/",
        "Input Directory": "$.params.inputs[0].s3|/data/",
        "Dataset Name": "$.params.dataset.name"
    }

    form_type_options = [
        "Cirro Dataset",
        "Input File",
        "Cirro Reference",
        "User-Provided Value"
    ]

    form_value_types = [
        "array",
        "boolean",
        "integer",
        "number",
        "string"
    ]
    deleted = False

    def __init__(
        self,
        kw: str,
        param_config: dict,
        workflow_config: 'WorkflowConfig'
    ):

        self.id: str = kw
        self.value: str = param_config["input"][kw]
        self.workflow_config = workflow_config

        # If the value is one of the hardcoded cases
        if self.value in self.input_type_values.values():
            self.input_type = {
                v: k for k, v in self.input_type_values.items()
            }[self.value]

        # If the value references a form element
        elif self.value.startswith("$.params.dataset.paramJson."):
            self.input_type = "Form Entry"

            # Find the location of the form which is referenced
            self.form_key = self.value[
                len("$.params.dataset.paramJson."):
            ].split(".")

            # Save the form elements of this param and all of its parents
            self.form_elements = {
                '.'.join(
                    self.form_key[:(i + 1)]
                ): self.get_form_element(
                    param_config,
                    self.form_key[:(i + 1)]
                )
                for i in range(len(self.form_key))
            }

            # Parse the form type
            if self.form_config.get("pathType") == "dataset":

                # Cirro Dataset
                if "process" in self.form_config:
                    self.form_type = "Cirro Dataset"
                # File from Input Cirro Dataset
                elif "file" in self.form_config:
                    self.form_type = "Input File"
                else:
                    raise Exception(
                        f"Expected 'process' or 'form' in {self.id}"
                    )

            # Cirro Reference
            elif self.form_config.get("pathType") == "references":
                self.form_type = "Cirro Reference"

                msg = "Expected 'file' for pathType: references"
                assert "file" in self.form_config, msg

                msg = "Reference 'file' must start with '**/'"
                assert self.form_config["file"].startswith("**/"), msg

                # Parse the reference ID and the file name
                self.reference_id = self.form_config[
                    "file"
                ][3:].split("/", 1)[0]
                self.reference_file = self.form_config[
                    "file"
                ].rsplit("/", 1)[-1]

            # Native React form element
            else:
                self.form_type = "User-Provided Value"

        # Fallback - hardcoded value
        else:
            self.input_type = "Hardcoded Value"

    @property
    def form_config(self) -> dict:
        return self.form_elements[".".join(self.form_key)]

    def get_form_element(self, param_config: dict, path: str):
        form = param_config["form"]["form"]
        # Iterate over the keys in the path
        for ix, kw in enumerate(path):

            # If the keyword is not in the form for some reason
            if kw not in form["properties"]:

                # If it is the terminal keyword
                if len(path) == len(self.form_key) and ix == len(path) - 1:

                    # Set it up as a simple string
                    form["properties"][kw] = dict(
                        type="string",
                        default=kw,
                        title=kw
                    )

                # If it is an internal node
                else:

                    # Set it up as a simple object
                    form["properties"][kw] = dict(
                        properties=dict(),
                        type="object"
                    )

            form = form["properties"][kw]

        return {
            kw: val
            for kw, val in form.items()
            if kw != "properties"
        }

    def dump(self, workflow_config: dict):

        if self.deleted:
            return

        # Populate the form element, along with all parent levels
        if self.input_type == "Form Entry":

            # If the user is to be presented with a reference input
            if self.form_type == "Cirro Reference":

                # Set up the file attribute
                reference_file = self.reference_file_display[
                    self.index_reference_file
                ]
                file_str = f"**/{self.reference_id}/**/{reference_file}"
                self.form_config["file"] = file_str

            # All new params will exist at the root level
            if "form_key" not in self.__dict__:
                self.form_key = [self.id]

            if "form_elements" not in self.__dict__:
                self.form_elements = {
                    '.'.join(self.form_key): {}
                }

            # Set up a pointer for navigating the form
            pointer = workflow_config["form"]["form"]

            for i in range(len(self.form_key)):
                if "properties" not in pointer:
                    pointer["properties"] = dict()

                val = self.form_elements['.'.join(self.form_key[:(i + 1)])]

                if self.form_key[i] not in pointer["properties"]:
                    pointer["properties"][self.form_key[i]] = val

                pointer = pointer["properties"][self.form_key[i]]

        # Populate the special-case hardcoded values
        elif self.input_type in self.input_type_values:
            self.value = self.input_type_values[self.input_type]

        # In all cases, add the value to the input spec
        workflow_config["input"][self.id] = self.value

    def serve(self, config: 'WorkflowConfig'):

        # Set up an expander for this parameter
        self.expander = config.params_container.expander(
            f"Input Parameter: '{self.id}'",
            expanded=True
        )

        # Let the user edit the parameter name
        self.text_input(
            "id",
            "Parameter ID",
            self.id,
            help="A unique key (with no spaces) used to identify the parameter"
        )

        # Set up a drop-down for the input type
        self.dropdown(
            "input_type",
            "Parameter Type",
            self.input_type_options,
            help="Select the way in which the user will set the parameter's value",
            index=self.input_type_options.index(self.input_type)
        )
        self.expander.write(self.input_type_descriptions[self.input_type])
        if self.input_type == "Form Entry":
            # Let the user set up the title and description
            self.text_input(
                "form.title",
                "Parameter Title",
                self.form_config.get("title", ""),
                help="Title displayed in the form to the user"
            )
            self.text_input(
                "form.description",
                "Parameter Description",
                self.form_config.get("description", ""),
                help="Longer description provided in the form to the user"
            )

            # Let the user modify the form type
            self.dropdown(
                "form_type",
                "Form Entry Type",
                self.form_type_options,
                self.form_type_options.index(self.form_type),
                help="Select the type of form element shown to the user to enter data"
            )

            # User-provided value (vanilla react form element)
            if self.form_type == "User-Provided Value":

                self.expander.write("""
The user will enter their own value which must adhere to the 
required type.
""")

                self.dropdown(
                    "form.type",
                    "Form Value Type",
                    self.form_value_types,
                    self.form_value_types.index(self.form_config["type"]),
                    help="Select the value type allowed for user entry"
                )

                if self.form_config["type"] == "string":

                    self.text_input(
                        "form.default",
                        "Default Value",
                        self.form_config.get('default', ""),
                    )

                elif self.form_config["type"] == "number":

                    self.number_input(
                        "form.default",
                        "Default Value",
                        self.form_config.get('default', ""),
                    )

                elif self.form_config["type"] == "integer":

                    self.integer_input(
                        "form.default",
                        "Default Value",
                        self.form_config.get('default', ""),
                    )

                elif self.form_config["type"] == "boolean":

                    if "value" not in self.form_config:
                        self.form_config["value"] = False
                    elif not isinstance(self.form_config["value"], bool):
                        self.form_config["value"] = False

                    self.dropdown(
                        "form.default",
                        "Default Value",
                        [True, False],
                        [True, False].index(self.form_config["value"])
                    )

            # Select a dataset as the input
            elif self.form_type == "Cirro Dataset":

                self.expander.write(
                    """
The user will select an existing dataset.
The workflow will be provided with the base URL which contains
the files in that dataset.
"""
                )

                # Select the dataset type to choose from
                self.dropdown(
                    "form.process",
                    "Select Dataset of Type:",
                    list_processes(ingest=True),
                    self.index_process_type,
                    help="Only datasets of this particular type will be shown to the user"  # noqa
                )

            # Select a file from the input dataset
            elif self.form_type == "Input File":

                self.expander.write("""
The user will select one (or more) files from either the entire
input dataset or a subset as defined by a pattern filter using
wildcards.
""")

                self.text_input(
                    "form.file",
                    "Filter Available Files with Pattern",
                    self.form_config.get("file", "**/*"),
                    help="Subset the files the user can select from which match the wildcard glob"  # noqa
                )
                self.dropdown(
                    "form.multiple",
                    "Allow Multiple File Selection",
                    [True, False],
                    [True, False].index(
                        self.form_config.get('multiple', False)
                    ),
                    help="Optionally allow the user to select multiple files"
                )

            # Select a Cirro reference object
            else:
                assert self.form_type == "Cirro Reference"

                self.expander.write("""
The user will select a reference object which has been
uploaded to their project.
""")

                # Select the reference type to choose from
                self.dropdown(
                    "reference_id",
                    "Reference Type",
                    self.reference_list_display,
                    self.index_reference_type,
                    help="Select the type of reference data the user must choose from"
                )

                # If there are multiple reference files available
                if len(self.reference_file_display) > 1:
                    # Select the reference file to choose from
                    self.dropdown(
                        "reference_file",
                        "Reference File",
                        self.reference_file_display,
                        self.index_reference_file,
                        help="Select the specific file from the reference data"
                    )

        # Just a value
        elif self.input_type == "Hardcoded Value":
            self.text_input(
                "value",
                "Value",
                self.value,
            )

        # Add a button to remove the parameter
        self.expander.button(
            "Remove",
            key=self.ui_key("_remove"),
            on_click=self.remove
        )

    @property
    def index_process_type(self) -> int:

        pid = self.form_config['process']

        for i, n in enumerate(list_processes(ingest=True)):
            if f"({pid})" in n:
                return i
        raise Exception(f"Could not find appropriate process for {pid}")

    @property
    def reference_list_display(self) -> List[str]:
        return [
            ref.name
            for ref in list_references()
        ]

    @property
    def index_reference_type(self) -> int:
        for i, ref in enumerate(list_references()):
            if ref.directory == self.reference_id:
                return i
            if ref.name == self.reference_id:
                return i
        msg = f"Could not find appropriate reference for {self.reference_id}"
        raise Exception(msg)

    @property
    def reference_file_display(self):
        # Get the reference object which was selected
        ref = list_references()[self.index_reference_type]

        # Return the list of files available
        return [
            file.to_dict()['saveAs']
            for file in ref.validation
        ]

    def find_reference_directory(self, ref_name):
        for ref in list_references():
            if ref.name == ref_name:
                return ref.directory
        raise Exception(f"Could not find reference: {ref_name}")

    @property
    def index_reference_file(self):
        """Return the index position of the selected file."""

        for i, file_name in enumerate(self.reference_file_display):
            if self.reference_file == file_name:
                return i
        return 0

    def update_attribute(self, kw: str):

        if self.ui_key(kw) not in st.session_state:
            return

        val = st.session_state[self.ui_key(kw)]

        # If we are updating the reference type
        if kw == "reference_id":

            # Map the human-readable name to the directory
            val = self.find_reference_directory(val)

        # If we are updating the form type
        if kw == "form_type":

            # Update the form type
            self.update_form_type(val)

        # If we are changing a form element
        elif kw.startswith("form."):

            # If we are modifying a process attribute
            if kw == "form.process":

                # Trim it down to the process id
                val = val.rsplit(" (", 1)[-1].rstrip(")")

            # If the value is the same
            if val == self.form_elements[
                ".".join(self.form_key)
            ].get(
                kw[len("form."):]
            ):
                # Take no action
                return

            # If the value is different, update the form
            # and then redraw the form (below)
            self.form_elements[
                ".".join(self.form_key)
            ][
                kw[len("form."):]
            ] = val

            # If the form input type was changed
            if kw == "form.type":

                # Set the new default value
                self.form_elements[
                    ".".join(self.form_key)
                ][
                    "default"
                ] = dict(
                    integer=0,
                    number=0.0,
                    string="",
                    boolean=False,
                    array=[]
                )[val]

        else:
            # If the value is the same
            if val == self.__dict__[kw]:
                # Take no action
                return
            # If the value is different, update the attribute
            # And then redraw the form (below)

            self.__dict__[kw] = val

            # If we are updating the parameter type
            if kw == "input_type":
                # If there is a hardcoded value
                if val in self.input_type_values:
                    self.value = self.input_type_values[val]

                # If we are turning something into a form entry
                elif val == "Form Entry":
                    # Set up the blank form attributes
                    self.value = f"$.params.dataset.paramJson.{self.id}"
                    self.form_type = "User-Provided Value"

                    self.form_elements = {
                        self.id: {
                            "type": "string",
                            "default": "",
                            "title": self.id,
                            "description": f"Description of {self.id}"
                        }
                    }

                else:
                    self.value = ""

        self.workflow_config.save_config()
        self.workflow_config.reset()

    def update_form_type(self, val):
        """Change the form input type."""

        # Get the form element
        form_element: dict = self.form_elements[
            ".".join(self.form_key)
        ]
        form_element["type"] = "string"

        # Vanilla react form schema
        if val == "User-Provided Value":

            # Delete any special-case attributes
            for kw in ["file", "pathType", "process"]:
                if kw in form_element:
                    del form_element[kw]

        # Custom element
        else:

            # Select a dataset
            if val == "Cirro Dataset":

                # Use the special pathType attribute
                form_element["pathType"] = "dataset"
                # Use the process attribute
                form_element["process"] = "paired_dnaseq"
                # Remove the "file" attribute, if any
                if "file" in form_element:
                    del form_element["file"]

            # Select a file from the input dataset
            elif val == "Input File":

                # Use the special pathType attribute
                form_element["pathType"] = "dataset"
                # Use the file attribute
                form_element["file"] = "**/*"
                # Remove the "process" attribute, if any
                if "process" in form_element:
                    del form_element["process"]

            # Select a reference object
            else:

                assert val == "Cirro Reference", f"Unexpected: {val}"

                # Use the special pathType attribute
                form_element["pathType"] = "references"
                form_element["file"] = "**/genome_fasta/**/genome.fasta"


class ParamsConfig(WorkflowConfigElement):

    params: List[Param]

    def load(self, config: dict) -> None:
        """
        Set up attributes based on the contents
        of the configuration JSON
        """

        # Set up an empty list of params
        self.params = []

        # Load params based on their being listed in the form
        assert "input" in config, json.dumps(config, indent=4)

        for kw in config["input"].keys():

            # Set up a param object for this keyword value
            self.params.append(
                Param(kw, config, self.workflow_config)
            )

    def dump(self, config: dict) -> None:
        """
        The attributes of the configuration will be
        populated based on the state of this element.
        """

        for param in self.params:
            param.dump(config)

    def serve(self, config: 'WorkflowConfig') -> None:
        """
        Serve the user interaction for modifying the element
        """

        for param in self.params:
            param.serve(config)

        # Provide a button to add a new parameter
        config.params_container.button(
            "Add Parameter",
            f"add_parameter.{self.workflow_config.form_ix}",
            on_click=self.add_parameter,
            args=(config,)
        )

    def add_parameter(self, config: 'WorkflowConfig') -> None:
        """Add a single parameter to the config."""

        # Find a unique parameter ID
        param_ix = 1
        while f"param_{param_ix}" in map(lambda p: p.id, self.params):
            param_ix += 1

        # Add the parameter
        self.params.append(
            Param(
                f"param_{param_ix}",
                dict(input={f"param_{param_ix}": ""}),
                config
            )
        )

        # Save the updated config
        config.save_config()
        config.reset()


class OutputMeltConfig(UIElement):

    ui_key_prefix = "output_melt"

    def __init__(self, dat: dict, id: str, workflow_config: 'WorkflowConfig'):
        self.enabled = dat is not None
        self.dat = dat
        self.id = id
        self.workflow_config = workflow_config

        if dat is not None:
            for kw1, val1 in dat.items():
                for kw2, val2 in val1.items():
                    self.__dict__[f"{kw1}_{kw2}"] = val2

    def serve(self, expander: DeltaGenerator):

        self.expander = expander

        self.dropdown(
            "enabled",
            "Melt Remaining Columns",
            [True, False],
            [True, False].index(self.enabled)
        )

        if self.enabled:
            for kw1, desc1 in [
                ("key", "column headers"),
                ("value", "table values")
            ]:
                for kw2, desc2 in [("name", "Name"), ("desc", "Description")]:
                    kw = f"{kw1}_{kw2}"
                    self.text_input(
                        value=self.__dict__[kw],
                        kw=kw,
                        title=f"{desc2} of data in {desc1}"
                    )

    def update_attribute(
        self,
        kw: str
    ):
        if self.ui_key(kw) not in st.session_state:
            return

        # Get the value from the input element
        val = st.session_state[self.ui_key(kw)]

        # If the value is the same
        if val == self.__dict__.get(kw):
            # Take no action
            return

        # If the value is different, update the attribute
        self.__dict__[kw] = val

        # And then redraw the form (below)
        self.workflow_config.save_config()
        self.workflow_config.reset()

    def dump(self) -> dict:
        return dict(
            key=dict(
                name=self.__dict__.get("key_name", ""),
                desc=self.__dict__.get("key_desc", "")
            ),
            value=dict(
                name=self.__dict__.get("value_name", ""),
                desc=self.__dict__.get("value_desc", "")
            )
        )


class OutputConcatConfig(UIElement):

    ui_key_prefix = "output_concat"

    def __init__(
        self,
        file_ix: int,
        token: str,
        name: str,
        desc: str,
        workflow_config: 'WorkflowConfig'
    ):
        self.id = f"file_{file_ix}_token_{token}"
        self.workflow_config = workflow_config
        self.token = token
        self.name = name
        self.desc = desc

    def serve(self, expander: DeltaGenerator):

        self.expander = expander

        self.expander.write("---")
        self.expander.write(
            f"File Path Token: {self.token}"
        )

        for kw, title, help in [
            (
                "name",
                "Token Name",
                "Display name for the information encoded in the token"
            ),
            (
                "desc",
                "Token Description",
                "Longer description of information encoded in the token"
            )
        ]:
            self.text_input(
                value=self.__dict__[kw],
                kw=kw,
                title=title,
                help=help
            )

    def update_attribute(
        self,
        kw: str
    ):
        if self.ui_key(kw) not in st.session_state:
            return

        # Get the value from the input element
        val = st.session_state[self.ui_key(kw)]

        # If the value is the same
        if val == self.__dict__.get(kw):
            # Take no action
            return

        # If the value is different, update the attribute
        self.__dict__[kw] = val

        # And then redraw the form (below)
        self.workflow_config.save_config()
        self.workflow_config.reset()

    def dump(self) -> dict:
        return dict(
            token=self.token,
            name=self.name,
            desc=self.desc
        )


class OutputColumnConfig(UIElement):

    ui_key_prefix = "output_column"

    def __init__(self, col: dict, id: str, workflow_config: 'WorkflowConfig'):
        self.col = col
        self.id = id
        self.workflow_config = workflow_config
        self.deleted = False

    def serve(self, expander: DeltaGenerator):

        self.expander = expander

        self.expander.write("---")
        for attr in [
            dict(
                kw="col",
                title="Column Header",
                help="Value in the header row for the column"
            ), dict(
                kw="name",
                title="Column Name",
                help="Name presented to the user for the values in the column"
            ), dict(
                kw="desc",
                title="Column Description",
                help="Longer description of data in the column"
            )
        ]:
            self.text_input(
                value=self.col.get(attr["kw"], ""),
                **attr
            )

        # Button to delete the column information
        expander.button(
            "Remove Column",
            key=self.ui_key("remove_column"),
            on_click=self.remove
        )

    def remove(self):
        self.deleted = True
        self.workflow_config.save_config()
        self.workflow_config.reset()

    def update_attribute(
        self,
        kw: str
    ):

        if self.ui_key(kw) not in st.session_state:
            return

        # Get the value from the input element
        val = st.session_state[self.ui_key(kw)]

        # If the value is the same
        if val == self.col.get(kw):
            # Take no action
            return

        # If the value is different, update the attribute
        self.col[kw] = val

        # And then redraw the form (below)
        self.workflow_config.save_config()
        self.workflow_config.reset()


class OutputConfig(UIElement):

    commands = ["hot.Parquet"]
    ui_key_prefix = "output"
    source_prefix = "$data_directory/"
    delimiters = dict(Tab="\t", Comma=",")
    TOKEN_REGEX = r"\[([A-Za-z]+)\]"

    def __init__(
        self,
        file_config: dict,
        file_ix: int,
        workflow_config: 'WorkflowConfig'
    ):
        self.file_config: dict = file_config
        self.workflow_config = workflow_config
        self.deleted = False
        self.id = file_ix

        assert "command" in self.file_config, "Missing 'command'"
        msg = f"Unrecognized: {self.command}"
        assert self.command in self.commands, msg

        # Set up minimal attributes for output types
        if self.command == "hot.Parquet":

            if "params" not in self.file_config:
                self.file_config["params"] = dict()
            if "cols" not in self.file_config["params"]:
                self.file_config["params"]["cols"] = []
            if "name" not in self.file_config["params"]:
                self.file_config["params"]["name"] = "Output File"

            # Set up the delimiter as a self attribute
            self.delimiter = self.file_config["params"].get(
                "read_csv", {}
            ).get(
                "parse", {}
            ).get(
                "delimiter", ","
            )

            # Set up the column attributes
            self.columns = [
                OutputColumnConfig(
                    col,
                    f"{self.id}.col_{col_ix}",
                    workflow_config
                )
                for col_ix, col in enumerate(
                    self.file_config["params"]["cols"]
                )
            ]

            # Set up the optional melt attributes
            self.melt = OutputMeltConfig(
                self.file_config.get("melt"),
                f"{self.id}.melt",
                workflow_config
            )

            # Set up the optional concat attributes
            self.concat = self.parse_concat_tokens()

    @property
    def tokens(self) -> List[str]:
        return re.findall(
            self.TOKEN_REGEX,
            self.source
        )

    def parse_concat_tokens(self) -> List[OutputConcatConfig]:
        """
        Parse any concat tokens from the source string.
        If the concat field is already present, use that
        for the name and description.
        """

        concat_configs = []

        # If there are no tokens, stop
        if len(self.tokens) == 0:
            return concat_configs

        # Key any existing concat name/desc by token
        existing_spec = {
            i["token"]: i
            for i in self.file_config["params"].get("concat", [])
            if isinstance(i, dict) and "token" in i
        }

        # Set up an OutputConcatConfig element for each
        for token in self.tokens:
            concat_configs.append(
                OutputConcatConfig(
                    file_ix=self.id,
                    token=token,
                    name=existing_spec.get(token, {}).get("name", token),
                    desc=existing_spec.get(token, {}).get("desc", token),
                    workflow_config=self.workflow_config
                )
            )

        return concat_configs

    def matches_regex(self, source) -> bool:
        """Check if the source string matches the provided regex."""
        regex_path = re.sub(self.TOKEN_REGEX, "(.*)", source)
        return re.search(regex_path, self.source) is not None

    @property
    def command(self) -> str:
        return self.file_config['command']

    @property
    def name(self) -> str:
        return self.file_config["params"]["name"]

    @property
    def source(self) -> str:
        return (self.file_config["params"]
                .get("source", self.source_prefix)
                [len(self.source_prefix):])

    @property
    def target(self):
        """Format the target based on the file path."""
        return self.source.replace("/", "_") + ".parquet"

    def update_delimiter(self):
        key = f"{self.id}_delimiter_{self.workflow_config.form_ix}"
        if key not in st.session_state:
            return
        val = st.session_state[key]
        val = self.delimiters[val]
        if val != self.delimiter:
            self.delimiter = val
            self.workflow_config.save_config()
            self.workflow_config.reset()

    def serve(self, config: 'WorkflowConfig'):
        """Serve the user interaction for this output file."""

        # Set up an expander for this element
        self.expander = config.outputs_container.expander(
            self.name,
            expanded=True
        )

        # Select the command type
        enum = ["hot.Parquet"]
        enumNames = ["Delimiter-Separated Values (CSV, TSV, etc.)"]
        self.dropdown(
            "command",
            "Data Encoding",
            enumNames,
            enum.index(self.command),
            kwargs=dict(names=dict(zip(enum, enumNames))),
            help="Serialization method used to save the data"
        )

        if self.command == "hot.Parquet":

            required = ["name", "desc", "source"]

            # Set the top-level attributes
            for attr in [
                dict(
                    kw="name",
                    title="Display Name",
                    value=self.name,
                    help="Name of file presented to the user in Cirro"
                ),
                dict(
                    kw="desc",
                    title="Description",
                    value=self.file_config["params"].get("desc", ""),
                    help="Full description of file presented in Cirro"
                ),
                dict(
                    kw="source",
                    title="File Path",
                    value=self.source,
                    help="File location within the output directory",
                    kwargs=dict(
                        transform=lambda v: f"{self.source_prefix}{v.strip('/')}"  # noqa
                    )
                ),
                dict(
                    kw="url",
                    title="Documentation URL (optional)",
                    help="Optionally provide a webpage documenting file contents",  # noqa
                    value=self.file_config["params"].get("url", "")
                )
            ]:
                kwargs = attr.get("kwargs", {})
                if "pointer" not in kwargs:
                    kwargs["pointer"] = self.file_config["params"]
                self.text_input(
                    kwargs=kwargs,
                    **{
                        k: v
                        for k, v in attr.items()
                        if k != "kwargs"
                    }
                )

                if attr["kw"] in required:

                    if self.file_config["params"].get(attr["kw"], "") == "":
                        self.expander.write(
                            f"Missing: Please provide {attr['title'].lower()}"
                        )

            # Set up a dropdown for the delimiter selection
            self.expander.selectbox(
                "Delimiter",
                self.delimiters.keys(),
                list(self.delimiters.values()).index(self.delimiter),
                key=f"{self.id}_delimiter_{self.workflow_config.form_ix}",
                on_change=self.update_delimiter,
                help="How the values are separated in the file"
            )

            for concat in self.concat:
                concat.serve(self.expander)

            for col in self.columns:
                col.serve(self.expander)

            # Let the user add a column
            self.expander.button(
                "Add Column",
                key=f"add_column_button.{self.id}.{self.workflow_config.form_ix}",  # noqa
                on_click=self.add_column
            )

            if len(self.columns) == 0:
                self.expander.write("Missing: Please define file columns")

            # Set up the user inputs to drive the melt command
            self.melt.serve(self.expander)

    def add_column(self):
        """Add a column for the file."""

        self.file_config["params"]["cols"].append(dict(
            col="New Column",
            name="New Column",
            desc="New Column"
        ))
        self.columns.append(
            OutputColumnConfig(
                self.file_config["params"]["cols"][-1],
                f"{self.id}.col_{len(self.columns)}",
                self.workflow_config
            )
        )
        self.workflow_config.save_config()
        self.workflow_config.reset()

    def update_attribute(
        self,
        kw: str,
        pointer=None,
        names=dict(),
        transform=None
    ):
        if self.ui_key(kw) not in st.session_state:
            return
        # Get the value from the input element
        val = st.session_state[self.ui_key(kw)]

        # Transform the value, if needed
        val = names.get(val, val)

        if transform is not None:
            val = transform(val)

        # If no pointer is provided
        if pointer is None:
            # Use the file_config
            pointer = self.file_config

        # If the value is the same
        if val == pointer.get(kw):
            # Take no action
            return

        # If the value is different, update the attribute
        pointer[kw] = val

        # And then redraw the form (below)
        self.workflow_config.save_config()
        self.workflow_config.reset()

    def dump(self) -> dict:
        """Write out the configuration."""

        if self.command == "hot.Parquet":
            # Set up the target kw
            self.file_config["params"]["target"] = self.target
            # Set up the delimiter
            self.file_config["params"]["read_csv"] = dict(
                parse=dict(
                    delimiter=self.delimiter
                )
            )
            # Set up the melt syntax
            if self.melt.enabled:
                self.file_config["melt"] = self.melt.dump()
            elif "melt" in self.file_config:
                del self.file_config["melt"]

            # Set up the concat syntax
            if len(self.concat) > 0:
                self.file_config["concat"] = [
                    concat.dump()
                    for concat in self.concat
                ]

            # Remove any deleted columns
            self.file_config["params"]["cols"] = [
                dat
                for col, dat in zip(
                    self.columns,
                    self.file_config["params"]["cols"]
                )
                if not col.deleted
            ]

        return self.file_config


class OutputsConfig(WorkflowConfigElement):

    def load(self, config: dict) -> None:
        """
        Set up attributes based on the contents
        of the configuration JSON
        """

        self.outputs: List[OutputConfig] = [
            OutputConfig(file_config, file_ix, self.workflow_config)
            for file_ix, file_config in enumerate(config["output"].get("commands", []))  # noqa
            if file_config.get("command") in OutputConfig.commands
        ]

        # Filter out any files which are matched by another regex
        while len(self.matching_regex) > 0:
            self.outputs = [
                output
                for i, output in enumerate(self.outputs)
                if i not in self.matching_regex
            ]

    def dump(self, config: dict) -> None:
        """
        Write out the description of all output files.
        """

        config["output"] = dict(
            commands=[
                output.dump()
                for output in self.outputs
                if not output.deleted
            ] + [
                dict(
                    command="hot.Manifest",
                    params=dict()
                )
            ]
        )

    def serve(self, config: 'WorkflowConfig') -> None:
        """
        Serve the user interaction for modifying each output file.
        """
    
        for output_file in self.outputs:
            output_file.serve(config)

        # Button to add an output
        config.outputs_container.button(
            "Add Output File",
            f"add_output_file_{self.workflow_config.form_ix}",
            on_click=self.add_output_file,
            args=(config,)
        )

    def add_output_file(self, config: 'WorkflowConfig') -> None:
        """
        Add a new output file to the list.
        """
        self.outputs.append(
            OutputConfig(
                dict(
                    command="hot.Parquet",
                    params=dict(
                        name=f"Output File {len(self.outputs) + 1}"
                    )
                ),
                len(self.outputs),
                config
            )
        )
        config.save_config()
        config.reset()

    @property
    def matching_regex(self) -> List[int]:
        """Indexes of outputs which do match another output's regex."""

        # Check each output
        for i, output in enumerate(self.outputs):

            # If there is a regex
            if len(output.tokens) > 0:

                # Make a list of all outputs which match
                # the regex (apart from itself)
                matching = [
                    j
                    for j, output2 in enumerate(self.outputs)
                    if i != j and output2.matches_regex(output.source)
                ]

                if len(matching) > 0:
                    return matching

        return []


class PreprocessConfig(WorkflowConfigElement):

    def load(self, config: dict) -> None:
        """
        Set up attributes based on the contents
        of the configuration JSON
        """
        self.preprocess = config["preprocess"]

    def dump(self, config: dict) -> None:
        """
        The attributes of the configuration will be
        populated based on the state of this element.
        """
        config["preprocess"] = self.preprocess

    def serve(self, config: 'WorkflowConfig') -> None:
        """
        Serve the user interaction for modifying the element
        """
        pass


class ComputeConfig(WorkflowConfigElement):

    def load(self, config: dict) -> None:
        """
        Set up attributes based on the contents
        of the configuration JSON
        """
        self.compute = config["compute"]

    def dump(self, config: dict) -> None:
        """
        The attributes of the configuration will be
        populated based on the state of this element.
        """
        config["compute"] = self.compute

    def serve(self, config: 'WorkflowConfig') -> None:
        """
        Serve the user interaction for modifying the element
        """
        pass


class WorkflowConfig:
    """Workflow configuration object."""

    elements: List[WorkflowConfigElement]
    login_empty: DeltaGenerator

    def __init__(self):

        # Set up configuration elements, each
        # of which is a WorkflowConfigElement
        self.elements = [
            SourceConfig(workflow_config=self),
            ParamsConfig(workflow_config=self),
            OutputsConfig(workflow_config=self),
            PreprocessConfig(workflow_config=self),
            ComputeConfig(workflow_config=self),
        ]

    def save_config(self) -> None:
        """Save a new copy of the config in the session state."""

        # Save the previous version
        self.save_history()

        # Update the session state
        st.session_state["config"] = self.format_config()

    def save_history(self):
        """Save the current config to history"""

        if "history" not in st.session_state:
            st.session_state["history"] = []

        if (
            st.session_state.get("config") is not None and
            (
                len(st.session_state["history"]) == 0 or
                st.session_state["history"][0] != st.session_state["config"]
            )
        ):
            st.session_state["history"].insert(0, st.session_state["config"])

            # After adding to the history, delete the future
            st.session_state["future"] = []

    def empty_config(self) -> dict:
        """Return an empty copy of the config."""

        # Make a blank copy
        config = {
            kw: default
            for kw, default in [
                ("dynamo", dict()),
                ("form", dict(form=dict(), ui=dict())),
                ("input", dict()),
                ("output", dict()),
                ("compute", ""),
                ("preprocess", "")
            ]
        }
        config["dynamo"]["code"] = dict()
        return config

    def format_config(self) -> dict:
        """Generate a config file based on the app state."""

        # Make a blank copy
        config = self.empty_config()

        # Populate the config based on the state of the form
        for element in self.elements:
            element.dump(config)

        return config

    def load_config(self) -> dict:
        """
        Load the configuration from the session state,
        filling in a default if not present.
        """

        return st.session_state.get(
            "config",
            dict(
                dynamo=dict(
                    code=dict()
                ),
                form=dict(form=dict(), ui=dict()),
                input=dict(),
                output=dict(),
                compute="",
                preprocess=""
            )
        )

    def serve(self):
        """
        Launch an interactive display allowing
        the user to configure the workflow.
        """

        # Set up tabs for the form and all generated elements
        tab_names = [
            "Analysis Workflow",
            "Input Parameters",
            "Output Files",
            "Cirro Configuration"
        ]
        self.tabs = dict(zip(tab_names, st.tabs(tab_names)))

        # Set up tabs for the configuration elements
        config_tabs = [
            "Dynamo",
            "Form",
            "Input",
            "Compute",
            "Preprocess",
            "Output"
        ]
        self.tabs = {
            **self.tabs,
            **dict(zip(
                config_tabs,
                self.tabs["Cirro Configuration"].tabs(
                    config_tabs
                )
            ))
        }

        # Set up an empty in each of the tabs
        self.tabs_empty: Dict[str, DeltaGenerator] = {
            kw: tab.empty()
            for kw, tab in self.tabs.items()
        }

        # Let the user upload files
        self.add_file_uploader()

        # Let the user parse example outputs
        self.parse_example_outputs()

        # Set up an empty which will be populated with "Download All" button
        self.download_all_empty = st.sidebar.empty()

        # Set up columns for the Undo and Redo buttons
        undo_col, redo_col = st.sidebar.columns(2)

        # Set up empty elements which will be populated with Undo/Redo
        self.undo_empty = undo_col.empty()
        self.redo_empty = redo_col.empty()

        # Set up a reset button
        st.sidebar.button(
            "Refresh",
            key="refresh_button",
            on_click=self.reset,
            help="Simply refresh the display"
        )

        # Set up a logout button
        st.sidebar.button(
            "Logout",
            key="logout_button",
            on_click=self.logout
        )

        # Populate the form and downloads
        self.reset()

    def reset(self):

        # Increment the index used for making unique element IDs
        self.form_ix = self.form_ix + 1

        # Populate the form
        self.populate_form()

        # Set up the download options
        self.populate_downloads()

    def logout(self):
        # Clear the session state
        st.session_state.clear()
        # Initialize the session configuration
        st.session_state["config"] = self.format_config()

    def populate_form(self):
        """Generate the form based on the configuration elements."""

        # Set up the containers
        self.form_container = self.tabs_empty["Analysis Workflow"].container()
        self.params_container = self.tabs_empty["Input Parameters"].container()
        self.outputs_container = self.tabs_empty["Output Files"].container()

        # Get the configuration from the session state
        config = self.load_config()

        # Iterate over each of the display elements
        for element in self.elements:
            # Load attributes from the configuration
            element.load(config)
            # Serve the interactivity of the configuration
            element.serve(self)

        # self.save_config()

    def populate_downloads(self):
        """Populate the options for downloading files"""

        # Create a zip file with all of the files
        zip_buffer = io.BytesIO()

        with zipfile.ZipFile(zip_buffer, "a") as zip_file:

            for kw, val in self.format_config().items():
                prefix = "" if kw == "preprocess" else "process-"
                ext = dict(
                    preprocess="py",
                    compute="config"
                ).get(kw, "json")
                file_name = f"{prefix}{kw}.{ext}"

                # Format the text of the element
                text = val if isinstance(val, str) else json.dumps(val, indent=4, sort_keys=True)  # noqa

                # Add to the zip file
                zip_file.writestr(file_name, text)

                # Replace the contents of the tab
                cont = self.tabs_empty[kw.title()].container()

                # Add a download button in the tab
                cont.download_button(
                    f"Download {file_name}",
                    text,
                    file_name=file_name,
                    key=f"download.{kw}.{self.form_ix}"
                )

                # Print the element in the tab
                cont.text(text)

        # Let the user download all files as a zip
        self.download_all_empty.download_button(
            "Download All Config Files (.zip)",
            zip_buffer,
            file_name="cirro-configuration.zip",
            key=f"download.all.{self.form_ix}"
        )

        # If there is any history
        if len(st.session_state.get("history", [])) > 0:
            # Add the undo button
            self.undo_empty.button(
                "Undo",
                key=f"undo.{self.form_ix}",
                on_click=self.undo,
                use_container_width=True
            )
        else:
            # If no history is present, clear the button
            self.undo_empty.empty()

        # If there is any future
        if len(st.session_state.get("future", [])) > 0:
            # Add the redo button
            self.redo_empty.button(
                "Redo",
                key=f"redo.{self.form_ix}",
                on_click=self.redo,
                use_container_width=True
            )
        else:
            # If no future is present, clear the button
            self.redo_empty.empty()

    def undo(self):
        """Action performed by the Undo button."""

        # The future is a list
        if "future" not in st.session_state:
            st.session_state["future"] = []

        # Put the current config in the future
        st.session_state["future"].insert(
            0, st.session_state["config"]
        )

        # Update the current state
        st.session_state["config"] = st.session_state["history"].pop(0)

        # Reset the display
        self.reset()

    def redo(self):
        """Action performed by the Redo button."""

        # Put the current config in the history
        if "history" not in st.session_state:
            st.session_state["history"] = []
        st.session_state["history"].insert(
            0, st.session_state["config"]
        )

        # Get the first config from the future
        # Update the current state
        st.session_state["config"] = st.session_state["future"].pop(0)

        # Reset the display
        self.reset()

    def add_file_uploader(self):

        # Let the user upload files
        upload_files = st.sidebar.expander(
            "Upload Configuration Files",
            expanded=False
        )
        upload_files.file_uploader(
            "Select Files",
            accept_multiple_files=True,
            key="uploaded_files"
        )
        upload_files.button(
            "Load Configuration from Files",
            on_click=self.load_from_uploaded_files
        )

    def load_from_uploaded_files(self):
        """Load configuration from uploaded files."""
        # Get the configuration from the session state
        config = st.session_state.get("config")

        # If the config has not been initialized, set up an empty dict
        if config is None:
            config = self.empty_config()

        # Keep track of whether any modifications have taken place
        modified = False

        for file in st.session_state.get("uploaded_files", []):
            msg = f"Did not expect input: {file.name}"
            if not file.name.startswith("process-"):
                assert file.name == "preprocess.py", msg
                config["preprocess"] = file.read().decode()
                modified = True
            elif file.name == "process-compute.config":
                config["compute"] = file.read().decode()
                modified = True
            else:
                if file.name.endswith(".json"):
                    key = file.name[len("process-"):-(len(".json"))]
                    assert key in ["dynamo", "form", "input", "output"], msg
                    config[key] = json.load(file)
                    modified = True

        if modified:

            # Save the previous version
            self.save_history()

            st.session_state["config"] = config
            # Redraw the form
            self.reset()

    @property
    def form_ix(self):
        return st.session_state.get("form_ix", 0)

    @form_ix.setter
    def form_ix(self, value):
        st.session_state["form_ix"] = value

    def parse_example_outputs(self) -> None:
        """
        Let the user parse a set of output files from an existing dataset.
        """

        self.example_data_expander = st.sidebar.expander(
            "Populate Outputs from Existing Dataset",
            expanded=False
        )

        # Select a project with the dataset of interest
        proj_key = f"parse_example.select_project.{self.form_ix}"
        self.example_data_expander.selectbox(
            "Cirro Project",
            list_projects(),
            index=list_projects().index(
                st.session_state.get("_selected_project", list_projects()[0])
            ),
            key=proj_key,
            on_change=self.update_session_state,
            args=("_selected_project", proj_key,),
            kwargs=dict(
                invalidate=["_selected_dataset", "_parse_examples_msg"]
            )
        )

        # Select the dataset from that project
        dataset_key = f"parse_example.select_dataset.{self.form_ix}"
        dataset_list = list_datasets_in_project(st.session_state[proj_key])
        self.example_data_expander.selectbox(
            "Dataset",
            dataset_list,
            index=dataset_list.index(
                st.session_state.get("_selected_dataset", dataset_list[0])
            ),
            key=dataset_key,
            on_change=self.update_session_state,
            args=("_selected_dataset", dataset_key,),
            kwargs=dict(invalidate=["_parse_examples_msg"])
        )

        ext_key = f"parse_example.file_ext.{self.form_ix}"
        self.example_data_expander.text_input(
            "File Extensions (comma-separated list)",
            value=st.session_state.get("_file_ext", "csv,tsv,txt"),
            help="Indicate the file extensions of files to check",
            key=ext_key,
            on_change=self.update_session_state,
            args=("_file_ext", ext_key),
            kwargs=dict(invalidate=["_parse_examples_msg"])
        )

        if st.session_state.get("_selected_dataset") is not None:

            # Get the list of files
            file_list = self.get_files_in_dataset()

            self.example_data_expander.button(
                f"Populate from {len(file_list)} Files",
                key="parse_example.execute",
                help="Read through the indicated dataset and parse all available files",  # noqa
                on_click=self.parse_example_dataset,
                args=(file_list,)
            )

        if st.session_state.get("_parse_examples_msg") is not None:
            self.example_data_expander.write(
                st.session_state.get("_parse_examples_msg")
            )

    def get_files_in_dataset(self) -> List[str]:
        """Get the files in the dataset which match the provided extensions."""

        extensions = st.session_state.get("_file_ext", "").split(",")

        # If no project is selected
        if st.session_state.get("_selected_project", "") == "":
            return []

        # If no dataset is selected
        if st.session_state.get("_selected_dataset", "") == "":
            return []

        return [
            fn
            for fn in list_files_in_dataset(
                st.session_state["_selected_project"],
                st.session_state["_selected_dataset"]
            )
            if (
                len(extensions) == 0 or
                any(["." + ext.strip(".") in fn for ext in extensions])
            )
        ]

    def parse_example_dataset(self, file_list) -> None:
        """Parse the files from the dataset."""

        if len(file_list) == 0:
            st.session_state["_parse_examples_msg"] = "No files found to parse"
            return

        # If no project is selected
        if st.session_state.get("_selected_project", "") == "":
            return

        # If no dataset is selected
        if st.session_state.get("_selected_dataset", "") == "":
            return

        # Get the dataset
        ds = get_dataset(
            st.session_state["_selected_project"],
            st.session_state["_selected_dataset"]
        )

        # Parse the list of terms (if not already cached)
        self.parse_terms()

        # Save the previous version
        self.save_history()

        if "config" not in st.session_state:
            st.session_state["config"] = self.format_config()

        # Replace the existing output spec
        st.session_state["config"]["output"] = dict(commands=[])

        for file_name in file_list:
            file_spec = self.parse_example_file(
                ds,
                file_name
            )
            if file_spec is not None:
                st.session_state["config"]["output"]["commands"].append(
                    file_spec
                )

        st.session_state["config"]["output"]["commands"].append(
            dict(
                command="hot.Manifest",
                params=dict()
            )
        )

        # Regenerate the display
        self.reset()

    def parse_example_file(self, ds: DataPortalDataset, file_name: str):

        # Try to read the table, checking for the different delimiters
        df = None
        for delim in ["\t", ","] if "tsv" in file_name else [",", "\t"]:

            try:
                df = ds.list_files().get_by_name(file_name).read_csv(
                    sep=delim,
                    nrows=5
                )
            except ValueError as e:  # noqa
                pass

            # If there is a single column, we assume it was not successful
            if df is not None and df.shape[1] <= 1:
                df = None

            if df is not None:
                break

        # If we couldn't read the table, stop here
        if df is None:
            return

        # If we can read the table, then format a description of each column
        cols = [
            dict(
                col=cname,
                **self.infer_column_name(cname, file_name)
            )
            for cname in df.columns.values
        ]

        msg = "Expected file to start with data/"
        assert file_name.startswith('data/'), msg
        file_name = file_name[len("data/"):]

        # Return the specification for this file
        return dict(
            command="hot.Parquet",
            params=dict(
                url="",
                source=f"$data_directory/{file_name}",
                target=file_name.replace("/", "_") + ".parquet",
                name=file_name.replace("/", "_").rsplit("/", 1)[0],
                desc=file_name.replace("/", "_").rsplit("/", 1)[0],
                read_csv=dict(
                    parse=dict(
                        delimiter=delim
                    )
                ),
                cols=cols
            )
        )

    def parse_terms(self):
        if st.session_state.get("_terms") is not None:
            return
        st.session_state["_terms"] = json.load(open("terms.json"))

    def infer_column_name(self, cname: str, file_name: str):
        """Return the pre-defined name and description, if any."""

        # Sanitize the column name before checking against the set of
        # predefined terms
        cname = re.sub('[^0-9a-zA-Z]+', '_', cname.lower().strip()).strip("_")
        while "__" in cname:
            cname = cname.replace("__", "_")

        # Check the set of predefined terms
        term: dict = st.session_state["_terms"].get(cname, {})

        # Iterate through the defined metadata in reverse order
        for meta in term.get("metadata", [])[::-1]:
            # If the file name matches (or if we get to the wild-card)
            if (
                file_name.replace("data/", "") == meta["file"].replace("data/", "")  # noqa
                or meta["file"] == "*"
            ):
                # Return the name and description
                return dict(
                    name=meta["name"],
                    desc=meta["desc"]
                )

        # If there is no match
        return dict(
            name=cname,
            desc=""
        )

    def update_session_state(self, dest, source, invalidate=[]):
        st.session_state[dest] = st.session_state.get(source, "")
        for kw in invalidate:
            if kw in st.session_state:
                del st.session_state[kw]


def configure_workflow_app():
    """Launch an interactive interface for configuring a workflow."""

    # Create a configuration object, loading any files that are already present
    config = WorkflowConfig()

    # Launch an interactive display allowing the user to modify
    # the workflow configuration
    config.serve()


if __name__ == "__main__":

    # Set up the page
    st.set_page_config(
        page_title="Cirro - Workflow Configuration",
        page_icon="https://cirro.bio/favicon-32x32.png"
    )
    st.header("Cirro - Workflow Configuration")

    # Get the options of the available tenants to log in
    tenants = {
        tenant["displayName"]: tenant["domain"]
        for tenant in list_tenants()
    }

    # Select the tenant
    base_url = tenants.get(
        st.selectbox(
            "Select your Cirro Instance",
            [""] + list(tenants.keys())
        )
    )

    # Log in to Cirro
    if base_url:
        login_empty = st.empty()
        cirro_login(base_url, login_empty)

        configure_workflow_app()
