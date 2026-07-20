# forecast_ui.py

from datetime import date, timedelta
import calendar
import ipywidgets as widgets
from ipywidgets import Layout, GridspecLayout

# ============================================================
#  DATE CONTROLLER CLASS
# ============================================================

class DateUIController:
    def __init__(
        self,
        year_picker,
        month_picker,
        day_picker,
        months,
        current_year,
        current_month,
        max_selectable_date,
        get_days_in_month
    ):
        self.year_picker = year_picker
        self.month_picker = month_picker
        self.day_picker = day_picker
        self.months = months
        self.current_year = current_year
        self.current_month = current_month
        self.max_selectable_date = max_selectable_date
        self.get_days_in_month = get_days_in_month

    # -------------------------
    # UPDATE MONTHS
    # -------------------------
    def update_month_options(self, change=None):
        from datetime import date

        selected_year = int(self.year_picker.value)

        if selected_year == self.current_year:
            # In the current year → restrict months
            if date.today().day < 3:
                self.month_picker.options = self.months[:self.current_month - 1]
                self.month_picker.value = self.months[self.current_month - 2]
            else:
                self.month_picker.options = self.months[:self.current_month]
                self.month_picker.value = self.months[self.current_month - 1]

            if self.months.index(self.month_picker.value) >= self.current_month:
                self.month_picker.value = self.months[self.current_month - 1]
        else:
            self.month_picker.options = self.months

    # -------------------------
    # UPDATE DAYS
    # -------------------------
    def update_day_options(self, change=None):
        selected_year = int(self.year_picker.value)
        selected_month = self.months.index(self.month_picker.value) + 1

        # Max days in this month
        max_days = self.get_days_in_month(selected_year, self.month_picker.value)

        # Restrict selection if it is the current month
        if selected_year == self.current_year and selected_month == self.current_month:
            max_days = min(max_days, self.max_selectable_date.day)

        # Update widget
        self.day_picker.options = [str(d) for d in range(1, max_days + 1)]
        default_day = max(1, min(self.max_selectable_date.day, max_days))
        self.day_picker.value = str(default_day)

    # -------------------------
    # GENERIC HANDLER
    # -------------------------
    def on_year_or_month_change(self, change=None):
        pass

    def get_selected_variables(self):
        """Return a list of variable names that are currently checked."""
        return [var for var, cb in self.variable_checkbox.items() if cb.value]

    def get_forecast_steps(self):
        """Return a list of forecast step ranges currently checked."""
        return [cb.description for cb in self.checkboxes2 if cb.value]

    def get_year(self):
        return int(self.year_picker.value)

    def get_month(self):
        return self.month_picker.value

    def get_day(self):
        return int(self.day_picker.value)


# ============================================================
#  WIDGET SETUP FUNCTION
# ============================================================

def build_forecast_ui():
    # ----------------------------------
    # DATE STATE
    # ----------------------------------
    current_date = date.today()
    current_year = current_date.year
    current_month = current_date.month
    max_selectable_date = current_date - timedelta(days=2)

    start_year = 2015
    years = [str(y) for y in range(start_year, current_year + 1)]

    months = [
        "January", "February", "March", "April", "May", "June",
        "July", "August", "September", "October", "November", "December"
    ]

    # ----------------------------------
    # WIDGETS
    # ----------------------------------
    year_picker = widgets.Dropdown(
        options=years,
        description="Year:",
        value=str(max_selectable_date.year)
    )

    month_picker = widgets.Dropdown(
        options=months,
        description="Month:",
        value=months[max_selectable_date.month - 1]
    )

    day_picker = widgets.Dropdown(description="Day:")

    # Function needed to determine days per month
    def get_days_in_month(year, month_name):
        month_num = months.index(month_name) + 1
        return calendar.monthrange(year, month_num)[1]

    # ----------------------------------
    # CONTROLLER
    # ----------------------------------
    controller = DateUIController(
        year_picker,
        month_picker,
        day_picker,
        months,
        current_year,
        current_month,
        max_selectable_date,
        get_days_in_month
    )

    # Connect observers
    year_picker.observe(controller.update_month_options, names='value')
    year_picker.observe(controller.update_day_options, names='value')
    year_picker.observe(controller.on_year_or_month_change, names='value')

    month_picker.observe(controller.update_day_options, names='value')
    month_picker.observe(controller.on_year_or_month_change, names='value')

    controller.update_month_options()
    controller.update_day_options()

    # ============================================================
    # ENSEMBLE MEAN CHECKBOX
    # ============================================================
    ensemble_checkbox = widgets.Checkbox(
        value=True,
        description='Use ensemble mean',
        indent=False
    )

    # ============================================================
    # FORECAST STEP CHECKBOX GRID
    # ============================================================
    grid2 = GridspecLayout(7, 7, grid_gap="0px")
    checkboxes2 = [
        widgets.Checkbox(
            description=f'{i*24}-{(i+1)*24}',
            value=True,
            indent=False,
            layout=Layout(width='100px', height='30px')
        )
        for i in range(46)
    ]

    cnt = 0
    for i in range(7):
        for j in range(7):
            if cnt < len(checkboxes2):
                grid2[i, j] = checkboxes2[cnt]
                cnt += 1

    select_all_btn2 = widgets.Button(description="Select All")
    clear_btn2 = widgets.Button(description="Clear")

    def select_all2(btn):
        for cb in checkboxes2:
            cb.value = True

    def clear_all2(btn):
        for cb in checkboxes2:
            cb.value = False

    select_all_btn2.on_click(select_all2)
    clear_btn2.on_click(clear_all2)

    # ============================================================
    # VARIABLES CHECKBOXES
    # ============================================================
    variables = [
        "10 metre U wind component", "10 metre V wind component",
        "Maximum temperature at 2 metres in the last 6 hours",
        "Minimum temperature at 2 metres in the last 6 hours",
        "Mean sea level pressure", "Total precipitation",
        "2 meter temperature", "Convective available potential energy",
        "Sea surface temperature", "Soil moisture top 20 cm",
        "Soil moisture top 100 cm", "Total column water"
    ]

    variable_checkbox = {
        var: widgets.Checkbox(
            description=var,
            value=True,
            indent=False,
            layout=Layout(width='350px', height='30px')
        ) for var in variables
    }

    half = len(variables) // 2 + (len(variables) % 2)
    left_column = [variable_checkbox[var] for var in variables[:half]]
    right_column = [variable_checkbox[var] for var in variables[half:]]

    two_column_layout = widgets.HBox([
        widgets.VBox(left_column),
        widgets.VBox(right_column)
    ])

    select_all_btn = widgets.Button(description="Select All")
    clear_btn = widgets.Button(description="Clear")

    def select_all(btn):
        for cb in variable_checkbox.values():
            cb.value = True

    def clear_all(btn):
        for cb in variable_checkbox.values():
            cb.value = False

    select_all_btn.on_click(select_all)
    clear_btn.on_click(clear_all)

    # ============================================================
    # FINAL DISPLAY LAYOUT
    # ============================================================
    display_layout = widgets.VBox([
        widgets.HTML("<b>Select year, month, and day:</b>"),
        widgets.HBox([year_picker, month_picker, day_picker]),

        widgets.HTML("<b>Download only ensemble mean or all 100 members:</b>"),
        ensemble_checkbox,

        widgets.HTML("<b>Select forecast step:</b>"),
        grid2,
        widgets.HBox([select_all_btn2, clear_btn2]),

        widgets.HTML("<b>Select variable:</b>"),
        two_column_layout,
        widgets.HBox([select_all_btn, clear_btn])
    ])

    controller = DateUIController(
    year_picker,
    month_picker,
    day_picker,
    months,
    current_year,
    current_month,
    max_selectable_date,
    get_days_in_month
    )

    controller.variable_checkbox = variable_checkbox
    controller.checkboxes2 = checkboxes2
    controller.ensemble_checkbox = ensemble_checkbox

    year_picker.observe(controller.update_month_options, names='value')
    year_picker.observe(controller.update_day_options, names='value')
    year_picker.observe(controller.on_year_or_month_change, names='value')

    month_picker.observe(controller.update_day_options, names='value')
    month_picker.observe(controller.on_year_or_month_change, names='value')



    return controller,display_layout
